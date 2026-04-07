// Prevents an additional console window on Windows in release mode.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::ShellExt;

/// Find a free TCP port on 127.0.0.1 by binding with port 0 and reading back
/// the OS-assigned port number.
fn find_free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .expect("Failed to bind to find a free port")
        .local_addr()
        .expect("Failed to get local address")
        .port()
}

/// Poll `http://127.0.0.1:{port}/health` until a 200 OK is received or the
/// timeout (10 seconds) is exceeded.  Uses only the standard library to avoid
/// adding a heavy async runtime dependency just for startup health-checking.
fn wait_for_server(port: u16) -> bool {
    use std::io::{Read, Write};
    use std::net::TcpStream;

    let addr = format!("127.0.0.1:{}", port);
    let request = format!(
        "GET /health HTTP/1.0\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n\r\n",
        port
    );

    for attempt in 0..40 {
        if attempt > 0 {
            std::thread::sleep(Duration::from_millis(250));
        }
        if let Ok(mut stream) = TcpStream::connect(&addr) {
            let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
            if stream.write_all(request.as_bytes()).is_ok() {
                let mut buf = [0u8; 32];
                if stream.read(&mut buf).is_ok() {
                    // HTTP/1.x 200 → starts with "HTTP/1"
                    if buf.starts_with(b"HTTP/1") && buf[9..12] == *b"200" {
                        return true;
                    }
                }
            }
        }
    }
    false
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let port = find_free_port();
            let handle = app.handle().clone();

            // Spawn the sidecar in a background thread so setup() returns quickly
            // and Tauri can finish initialising the window.
            std::thread::spawn(move || {
                let sidecar = handle
                    .shell()
                    .sidecar("scathach-server")
                    .expect("scathach-server sidecar not found — run PyInstaller first");

                let (mut _rx, child) = sidecar
                    .args(["--port", &port.to_string()])
                    .spawn()
                    .expect("Failed to launch scathach-server sidecar");

                // Store child PID in app state so we can kill it on shutdown.
                handle.manage(SidecarChild(std::sync::Mutex::new(Some(child))));

                // Wait until the server is ready.
                if !wait_for_server(port) {
                    eprintln!("[scathach] Warning: server did not respond on port {port} within 10s");
                }

                // Inject the port into the webview so api.ts can build the base URL.
                let js = format!("window.__SCATHACH_API_PORT__ = {};", port);
                if let Some(window) = handle.get_webview_window("main") {
                    let _ = window.eval(&js);
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                // Terminate the sidecar when the window is closed.
                if let Some(state) = app_handle.try_state::<SidecarChild>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        });
}

/// Wrapper so the sidecar child handle can live in Tauri's managed state.
struct SidecarChild(std::sync::Mutex<Option<tauri_plugin_shell::process::CommandChild>>);
