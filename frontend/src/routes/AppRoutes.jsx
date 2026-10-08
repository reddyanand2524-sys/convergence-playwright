import { Routes, Route, Navigate } from "react-router-dom";

import Login from "../pages/Auth/LoginPage.jsx";
import Register from "../pages/Auth/RegisterPage.jsx";
import ForgotPasswordPage from "../pages/Auth/ForgotPasswordPage.jsx";
import ResetPasswordPage from "../pages/Auth/ResetPasswordPage.jsx";
import PasswordResetSuccessPage from "../pages/Auth/PasswordResetSuccessPage.jsx";

import ProtectedRoute from "../components/ProtectedRoute.jsx";
import AppShell from "../layouts/AppShell.jsx";
import DashboardPage from "../pages/DashboardPage.jsx";
import TestsPage from "../pages/TestsPage.jsx";
import ProfilePage from "../pages/ProfilePage.jsx";
import ProjectWorkspace, {
  ProjectIndexRedirect,
} from "../pages/projects/ProjectWorkspace.jsx";
import ProjectConfigurationPage from "../pages/projects/ProjectConfigurationPage.jsx";
import ProjectRunPage from "../pages/projects/ProjectRunPage.jsx";
import RunResultsPage from "../pages/projects/RunResultsPage.jsx";
import SchedulesPage from "../pages/projects/SchedulesPage.jsx";
import RunHistoryPage from "../pages/projects/RunHistoryPage.jsx";
import RunDetailsPage from "../pages/projects/RunDetailsPage.jsx";
import ProjectFilesPage from "../pages/projects/ProjectFilesPage.jsx";
import ProjectFileEditPage from "../pages/projects/ProjectFileEditPage.jsx";
import ProjectFileViewerPage from "../pages/projects/ProjectFileViewerPage.jsx";

export default function AppRoutes() {
  return (
    <Routes>
      {/* Authentication */}
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Register />} />
      <Route path="/register" element={<Navigate to="/signup" replace />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/password-reset-success" element={<PasswordResetSuccessPage />} />

      {/* Authenticated application */}
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route path="/home" element={<DashboardPage />} />
          <Route path="/tests" element={<TestsPage />} />
          <Route path="/profile" element={<ProfilePage />} />

          <Route path="/projects/:projectId" element={<ProjectWorkspace />}>
            <Route index element={<ProjectIndexRedirect />} />
            <Route path="configuration" element={<ProjectConfigurationPage />} />
            <Route path="run" element={<ProjectRunPage />} />
            <Route path="history" element={<RunHistoryPage />} />
            <Route path="schedules" element={<SchedulesPage />} />
            <Route path="runs/:runId" element={<RunDetailsPage />} />
            <Route path="runs/:runId/results" element={<RunResultsPage />} />
            <Route path="files" element={<ProjectFilesPage />} />
            <Route path="files/:fileId" element={<ProjectFileViewerPage />} />
            <Route path="files/:fileId/edit" element={<ProjectFileEditPage />} />
          </Route>
        </Route>
      </Route>

      {/* Default route */}
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
