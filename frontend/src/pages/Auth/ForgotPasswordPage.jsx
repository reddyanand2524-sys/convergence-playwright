import { useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";
import { KeyRound, Mail } from "lucide-react";

import { forgotPassword } from "../../services/api";

function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();

    setSubmitted(false);
    setError("");

    try {
      setLoading(true);

      await forgotPassword(email.trim());

      setSubmitted(true);
    } catch (error) {
      setError(
        error?.message ||
          "Unable to process your request. Please try again."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <motion.div
        className="auth-shell forgot-password-shell"
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.6, ease: "easeOut" }}
      >
        <div className="forgot-password-card">

          {/* Icon */}
          <div className="forgot-password-icon">
            <KeyRound size={30} strokeWidth={2} />
          </div>

          {/* Heading */}
          <div className="forgot-password-header">
            <h1>Forgot Password?</h1>

            <p>
              Enter your registered email to request a password reset link.
            </p>
          </div>

          {/* Divider */}
          <div className="forgot-password-divider"></div>

          {/* Form */}
          <form
            className="forgot-password-form"
            onSubmit={handleSubmit}
          >
            {/* Email */}
            <div className="auth-field">
              <label htmlFor="email">
                Registered Email
              </label>

              <div className="auth-input-wrapper">
                <Mail size={18} />

                <input
                  id="email"
                  type="email"
                  placeholder="Enter your registered email"
                  value={email}
                  onChange={(event) => {
                    setEmail(event.target.value);
                    setSubmitted(false);
                    setError("");
                  }}
                  required
                />
              </div>
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              className="auth-button"
              disabled={loading}
            >
              {loading ? "Sending..." : "Send link"}
            </button>

            {/* Success Message */}
            {submitted && (
              <p
                className="auth-success"
                role="status"
                aria-live="polite"
              >
                If an account exists for this email, a password reset
                link has been sent.
              </p>
            )}

            {/* Error Message */}
            {error && (
              <p
                className="auth-error"
                role="alert"
              >
                {error}
              </p>
            )}
          </form>
          <Link to="/login" className="forgot-password-back">
            ← Back to Login
          </Link>
        </div>
      </motion.div>
    </div>
  );
}

export default ForgotPasswordPage;