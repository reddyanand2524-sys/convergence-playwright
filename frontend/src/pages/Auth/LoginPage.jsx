import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import AuthHero from "../../components/brand/AuthHero.jsx";
import LoadingState from "../../components/brand/LoadingState.jsx";
import ProgressBar from "../../components/brand/ProgressBar.jsx";
import StatusBar from "../../components/brand/StatusBar.jsx";
import useBackendHealth from "../../hooks/useBackendHealth.js";
import { loginUser } from "../../services/api";

// Real phases of the sign-in request (no simulated work).
const STEPS = {
  verifying: { title: "Signing you in", step: "Verifying credentials…", progress: 40 },
  ready: { title: "Workspace ready", step: "Credentials verified ✓", progress: 100 },
};

export default function LoginPage() {
  const navigate = useNavigate();
  const health = useBackendHealth();
  const attempt = useRef(0);
  const timer = useRef(null);
  const abortRef = useRef(null);

  const [showPassword, setShowPassword] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [phase, setPhase] = useState("form"); // form | verifying | ready

  useEffect(
    () => () => {
      clearTimeout(timer.current);
      abortRef.current?.abort();
    },
    []
  );

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");

    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }

    const id = ++attempt.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setPhase("verifying");

    try {
      const data = await loginUser(email.trim(), password, controller.signal);

      // Cancelled while the request was in flight: discard the result.
      if (id !== attempt.current) return;

      // JWT used by protected APIs such as GET /api/auth/me.
      localStorage.setItem("access_token", data.access_token);
      localStorage.setItem("current_user", JSON.stringify(data.user));

      setPhase("ready");
      timer.current = setTimeout(() => navigate("/home", { replace: true }), 450);
    } catch (err) {
      if (id !== attempt.current) return;
      setError(err?.message || "Login failed. Please check your email and password.");
      setPhase("form");
    }
  }

  function cancel() {
    // Aborts the in-flight request; the attempt counter also discards any
    // response that was already on its way back.
    abortRef.current?.abort();
    attempt.current += 1;
    clearTimeout(timer.current);
    setPhase("form");
  }

  const busy = phase !== "form";
  const current = STEPS[phase];

  return (
    <div className="auth-page">
      <div className="auth-shell">
        <div className="auth-visual-panel">
          <AuthHero />
        </div>

        <div className="auth-panel">
          {!busy ? (
            <div className="auth-form-wrap cv-fade">
              <form className="auth-form cv-form" onSubmit={handleSubmit} noValidate>
                <div className="cv-form-head">
                  <span className="cv-eyebrow">Sign in</span>
                  <h1>Welcome back</h1>
                </div>

                <label className="cv-field">
                  <span className="cv-label">Work email</span>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@company.com"
                    autoComplete="email"
                    required
                    aria-invalid={error && !email.includes("@") ? "true" : undefined}
                    aria-describedby={error ? "login-error" : undefined}
                  />
                </label>

                <div className="cv-field">
                  <span className="cv-label cv-label-row">
                    <label htmlFor="login-password">Password</label>
                    <Link to="/forgot-password">Forgot?</Link>
                  </span>
                  <span className="cv-password">
                    <input
                      id="login-password"
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      autoComplete="current-password"
                      required
                      aria-describedby={error ? "login-error" : undefined}
                    />
                    <button
                      type="button"
                      className="cv-password-toggle"
                      onClick={() => setShowPassword((value) => !value)}
                      aria-pressed={showPassword}
                      aria-label={showPassword ? "Hide password" : "Show password"}
                    >
                      {showPassword ? "HIDE" : "SHOW"}
                    </button>
                  </span>
                </div>

                {error && (
                  <div className="cv-alert is-critical" id="login-error" role="alert">
                    <span className="cv-alert-icon" aria-hidden="true">!</span>
                    {error}
                  </div>
                )}

                <button type="submit" className="auth-button cv-submit">
                  Sign in
                </button>

                <p className="cv-switch">
                  New to Convergence? <Link to="/signup">Sign up</Link>
                </p>
              </form>
            </div>
          ) : (
            <div className="cv-signing cv-fade">
              <LoadingState type="flow" label="" className="cv-signing-loader" />
              <div className="cv-signing-text" role="status" aria-live="polite">
                <div className="cv-signing-title">{current.title}</div>
                <div className="cv-signing-step">{current.step}</div>
              </div>
              <ProgressBar value={current.progress} label="Sign-in progress" />
              {phase === "verifying" && (
                <button type="button" className="cv-ghost" onClick={cancel}>
                  Cancel
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      <StatusBar health={health} />
    </div>
  );
}
