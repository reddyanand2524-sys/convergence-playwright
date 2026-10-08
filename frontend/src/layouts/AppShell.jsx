import { useCallback, useEffect, useState } from "react";
import {
  Link,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  ChevronRight,
  CircleUserRound,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  Plus,
} from "lucide-react";

import CreateProjectModal from "../components/CreateProjectModal.jsx";
import { getMe } from "../services/authApi.js";
import useBackendHealth from "../hooks/useBackendHealth.js";
import StatusBar from "../components/brand/StatusBar.jsx";
import BrandLogo from "../components/brand/BrandLogo.jsx";
import LoadingState from "../components/brand/LoadingState.jsx";
import { listProjects } from "../services/projectsApi.js";
import { clearSession, describeError } from "../services/authedFetch.js";


const navigation = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, to: "/home" },
  { id: "tests", label: "Tests", icon: FlaskConical, to: "/tests" },
  { id: "profile", label: "Profile", icon: CircleUserRound, to: "/profile" },
];

function readUser() {
  try {
    return JSON.parse(localStorage.getItem("current_user") || "{}");
  } catch {
    return {};
  }
}

function activeIdFor(pathname) {
  if (pathname.startsWith("/profile")) return "profile";
  if (pathname.startsWith("/tests") || pathname.startsWith("/projects")) {
    return "tests";
  }
  return "dashboard";
}

/*
 * Shared authenticated shell: sidebar, top bar, main content area.
 * Also owns data shared by several pages (current user, project list,
 * backend health) and exposes it to child routes via Outlet context.
 */
export default function AppShell() {
  const navigate = useNavigate();
  const { pathname } = useLocation();

  const [user, setUser] = useState(readUser);
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  // projects: { status: "loading" | "ready" | "error", items, error }
  const [projects, setProjects] = useState({
    status: "loading",
    items: [],
    error: "",
  });

  const health = useBackendHealth();

  const activeId = activeIdFor(pathname);
  const currentView = navigation.find((item) => item.id === activeId);

  // Current user: GET /auth/me. The localStorage value is only first paint.
  useEffect(() => {
    const controller = new AbortController();

    getMe(controller.signal)
      .then((me) => {
        const nextUser = me?.user || me;
        if (nextUser && typeof nextUser === "object") {
          setUser(nextUser);
          localStorage.setItem("current_user", JSON.stringify(nextUser));
        }
      })
      .catch(() => {
        // 401 is handled centrally by authedFetch/ProtectedRoute.
        // Other failures keep the cached user.
      });

    return () => controller.abort();
  }, []);

  const reloadProjects = useCallback(async (signal) => {
    setProjects((prev) => ({ ...prev, status: "loading", error: "" }));

    try {
      const items = await listProjects(signal);
      setProjects({
        status: "ready",
        items: Array.isArray(items) ? items : [],
        error: "",
      });
    } catch (err) {
      if (err?.name === "AbortError") return;
      setProjects({ status: "error", items: [], error: describeError(err) });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    reloadProjects(controller.signal);
    return () => controller.abort();
  }, [reloadProjects]);

  function signOut() {
    clearSession();
    navigate("/login", { replace: true });
  }

  function handleCreated(project, uploadError) {
    setIsCreateOpen(false);
    reloadProjects();
    navigate(`/projects/${project.id}/files`, {
      state: uploadError ? { uploadError } : { created: true },
    });
  }

  const outletContext = {
    user,
    projects,
    reloadProjects,
    health,
    openCreateProject: () => setIsCreateOpen(true),
  };

  return (
    <div className="home-app">
      <aside className="home-sidebar" aria-label="Main navigation">
        <div className="home-brand">
          <Link to="/home" className="home-brand-link" aria-label="Convergence home">
            <BrandLogo variant="lockup" surface="dark" height={34} className="brand-lockup" />
            <BrandLogo variant="mark" surface="dark" height={34} className="brand-mark-only" decorative />
          </Link>
        </div>

        <div className="home-nav-label">Workspace</div>

        <nav className="home-nav">
          {navigation.map(({ id, label, icon: Icon, to }) => (
            <Link
              key={id}
              to={to}
              className={`home-nav-item${activeId === id ? " is-active" : ""}`}
              aria-label={label}
              aria-current={activeId === id ? "page" : undefined}
            >
              <Icon size={21} strokeWidth={1.9} aria-hidden="true" />
              <span>{label}</span>
              <ChevronRight
                className="home-nav-chevron"
                size={16}
                aria-hidden="true"
              />
            </Link>
          ))}
        </nav>

        <button className="home-signout" type="button" onClick={signOut}>
          <LogOut size={20} aria-hidden="true" />
          <span>Sign out</span>
        </button>
      </aside>

      <div className="home-main">
        <header className="home-topbar">
          <div>
            <span className="home-top-eyebrow">Convergence</span>
            <h1>{currentView?.label || "Dashboard"}</h1>
          </div>

          <button
            className="home-primary-button"
            type="button"
            onClick={() => setIsCreateOpen(true)}
          >
            <Plus size={19} aria-hidden="true" />
            <span>Create Project</span>
          </button>
        </header>

        {projects.status === "loading" && (
          <LoadingState type="bar" label="Loading workspace" className="cv-topbar-loader" />
        )}

        <main className="home-content" key={pathname.split("/")[1]}>
          <Outlet context={outletContext} />
        </main>

        <StatusBar health={health} />
      </div>

      {isCreateOpen && (
        <CreateProjectModal
          onClose={() => setIsCreateOpen(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
}
