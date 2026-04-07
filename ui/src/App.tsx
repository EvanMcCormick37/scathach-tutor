import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import TopicDetail from "./pages/TopicDetail";
import SessionWizard from "./pages/SessionWizard";
import SessionScreen from "./pages/SessionScreen";
import ReviewScreen from "./pages/ReviewScreen";
import SuperReviewScreen from "./pages/SuperReviewScreen";
import StatsScreen from "./pages/StatsScreen";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/topics/:topicId" element={<TopicDetail />} />
        <Route path="/topics/:topicId/new-session" element={<SessionWizard />} />
        <Route path="/sessions/:sessionId" element={<SessionScreen />} />
        <Route path="/review" element={<ReviewScreen />} />
        <Route path="/super-review" element={<SuperReviewScreen />} />
        <Route path="/stats" element={<StatsScreen />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
