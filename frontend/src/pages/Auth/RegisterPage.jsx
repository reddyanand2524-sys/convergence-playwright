import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import {
  User,
  Mail,
  Phone,
  Lock,
  Eye,
  EyeOff,
} from "lucide-react";

import AuthHero from "../../components/brand/AuthHero.jsx";
import StatusBar from "../../components/brand/StatusBar.jsx";
import useBackendHealth from "../../hooks/useBackendHealth.js";

import { registerUser } from "../../services/api";

export default function RegisterPage() {
  const navigate = useNavigate();
  const health = useBackendHealth();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [mobile, setMobile] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] =
    useState(false);

  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();

    setError("");

    if (!name.trim()) {
      setError("Enter your full name.");
      return;
    }

    if (!email.trim()) {
      setError("Enter your email address.");
      return;
    }

    if (!mobile.trim()) {
      setError("Enter your mobile number.");
      return;
    }

    if (mobile.length !== 10) {
      setError(
        "Enter a valid 10-digit mobile number."
      );
      return;
    }

    if (!password) {
      setError("Enter a password.");
      return;
    }

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    try {
      setLoading(true);

      await registerUser({
        full_name: name.trim(),
        email: email.trim(),
        mobile_number: mobile,
        password,
        confirm_password: confirmPassword,
      });

      /*
       * Registration was successful.
       *
       * We don't automatically log the user in here.
       * The backend only creates the account.
       *
       * Send the user to the Login page.
       */
      navigate("/login", {
        replace: true,
        state: {
          registered: true,
          email: email.trim(),
        },
      });

    } catch (err) {
      setError(
        err?.message ||
        "Registration failed. Please try again."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-shell register-shell">

        <div className="auth-visual-panel">
          <AuthHero
            headline="Build. Run. Analyze."
            copy="Create your workspace and bring tests, traces and logs into one pipeline."
          />
        </div>

        <div className="auth-panel register-panel">

          <motion.div
            className="auth-form-wrap register-form-wrap"
            initial={{
              opacity: 0,
              y: 20,
            }}
            animate={{
              opacity: 1,
              y: 0,
            }}
            transition={{
              duration: 0.5,
            }}
          >

            <h1>Create account</h1>

            <p className="auth-subtitle">
              Create your account to access the automation workspace.
            </p>


            <form
              className="auth-form"
              onSubmit={handleSubmit}
            >

              {/* FULL NAME */}

              <div className="auth-input-group">

                <User
                  className="input-icon"
                  size={18}
                />

                <input
                  type="text"
                  value={name}
                  onChange={(e) =>
                    setName(e.target.value)
                  }
                  placeholder="Full name"
                  autoComplete="name"
                  required
                  disabled={loading}
                />

              </div>


              {/* EMAIL */}

              <div className="auth-input-group">

                <Mail
                  className="input-icon"
                  size={18}
                />

                <input
                  type="email"
                  value={email}
                  onChange={(e) =>
                    setEmail(e.target.value)
                  }
                  placeholder="Email"
                  autoComplete="email"
                  required
                  disabled={loading}
                />

              </div>


              {/* MOBILE */}

              <div className="auth-input-group">

                <Phone
                  className="input-icon"
                  size={18}
                />

                <input
                  type="tel"
                  value={mobile}
                  onChange={(e) =>
                    setMobile(
                      e.target.value
                        .replace(/\D/g, "")
                        .slice(0, 10)
                    )
                  }
                  placeholder="Mobile number"
                  autoComplete="tel"
                  inputMode="numeric"
                  maxLength={10}
                  required
                  disabled={loading}
                />

              </div>


              {/* PASSWORD */}

              <div className="auth-input-group password-group">

                <Lock
                  className="input-icon"
                  size={18}
                />

                <input
                  type={
                    showPassword
                      ? "text"
                      : "password"
                  }
                  value={password}
                  onChange={(e) =>
                    setPassword(e.target.value)
                  }
                  placeholder="Password"
                  autoComplete="new-password"
                  required
                  disabled={loading}
                />

                <button
                  type="button"
                  className="password-toggle"
                  onClick={() =>
                    setShowPassword(
                      (value) => !value
                    )
                  }
                  aria-label={
                    showPassword
                      ? "Hide password"
                      : "Show password"
                  }
                  disabled={loading}
                >
                  {showPassword ? (
                    <Eye size={18} />
                  ) : (
                    <EyeOff size={18} />
                  )}
                </button>

              </div>


              {/* CONFIRM PASSWORD */}

              <div className="auth-input-group password-group">

                <Lock
                  className="input-icon"
                  size={18}
                />

                <input
                  type={
                    showConfirmPassword
                      ? "text"
                      : "password"
                  }
                  value={confirmPassword}
                  onChange={(e) =>
                    setConfirmPassword(
                      e.target.value
                    )
                  }
                  placeholder="Confirm password"
                  autoComplete="new-password"
                  required
                  disabled={loading}
                />

                <button
                  type="button"
                  className="password-toggle"
                  onClick={() =>
                    setShowConfirmPassword(
                      (value) => !value
                    )
                  }
                  aria-label={
                    showConfirmPassword
                      ? "Hide confirm password"
                      : "Show confirm password"
                  }
                  disabled={loading}
                >
                  {showConfirmPassword ? (
                    <Eye size={18} />
                  ) : (
                    <EyeOff size={18} />
                  )}
                </button>

              </div>


              {/* ERROR */}

              {error && (
                <p className="auth-error">
                  {error}
                </p>
              )}


              {/* REGISTER BUTTON */}

              <motion.button
                type="submit"
                className="auth-button"
                whileHover={
                  !loading
                    ? { y: -1 }
                    : undefined
                }
                whileTap={
                  !loading
                    ? { scale: 0.99 }
                    : undefined
                }
                disabled={loading}
              >
                {loading
                  ? "Creating account..."
                  : "Create account"}
              </motion.button>

            </form>


            <div className="auth-links">

              <p>
                Already have an account?{" "}
                <Link to="/login">
                  Sign in
                </Link>
              </p>

            </div>

          </motion.div>

        </div>

      </div>

      <StatusBar health={health} />
    </div>
  );
}
