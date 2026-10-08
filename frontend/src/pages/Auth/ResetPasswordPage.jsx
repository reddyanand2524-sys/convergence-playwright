import { useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { Eye, EyeOff, Lock } from "lucide-react";
import { motion } from "motion/react";

import { resetPassword } from "../../services/api";

function ResetPasswordPage() {
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const navigate = useNavigate();
  const location = useLocation();

  /*
   * The reset link from the backend looks like:
   *
   * http://localhost:5173/reset-password?token=ABC123
   *
   * URLSearchParams allows us to extract:
   *
   * token = ABC123
   */
  const searchParams = new URLSearchParams(location.search);
  const token = searchParams.get("token");

  const handleSubmit = async (event) => {
    event.preventDefault();

    setError("");

    // Check whether reset token exists
    if (!token) {
      setError(
        "Invalid or missing password reset link. Please request a new reset link."
      );
      return;
    }

    // Check password fields
    if (!newPassword || !confirmPassword) {
      setError("Please fill in both password fields.");
      return;
    }

    // Backend requires minimum 8 characters
    if (newPassword.length < 8) {
      setError("Password must contain at least 8 characters.");
      return;
    }

    // Check passwords match
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match. Please try again.");
      return;
    }

    try {
      setLoading(true);

      /*
       * Call backend:
       *
       * POST /api/auth/reset-password
       *
       * Body:
       * {
       *   token,
       *   new_password,
       *   confirm_password
       * }
       */
      await resetPassword(
        token,
        newPassword,
        confirmPassword
      );

      /*
       * Password was successfully changed.
       *
       * Navigate to the existing password reset success page.
       */
      navigate("/password-reset-success");
    } catch (error) {
      setError(
        error?.message ||
          "Unable to reset your password. Please try again."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <motion.div
        className="reset-password-shell"
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.6, ease: "easeOut" }}
      >
        {/* Lock Icon */}
        <div className="reset-password-icon">
          <Lock size={28} strokeWidth={2} />
        </div>

        {/* Heading */}
        <div className="reset-password-header">
          <h1>Set New Password</h1>

          <p>
            Create a new password for your account.
            Make sure it is strong and secure.
          </p>
        </div>

        {/* Divider */}
        <div className="reset-password-divider"></div>

        <form onSubmit={handleSubmit}>
          {/* New Password */}
          <div className="reset-password-field">
            <label htmlFor="new-password">
              New Password
            </label>

            <div className="password-input-wrapper">
              <input
                id="new-password"
                type={
                  showNewPassword
                    ? "text"
                    : "password"
                }
                placeholder="Enter new password"
                value={newPassword}
                onChange={(event) => {
                  setNewPassword(event.target.value);
                  setError("");
                }}
                minLength={8}
                required
              />

              <button
                type="button"
                className="password-toggle"
                onClick={() =>
                  setShowNewPassword(
                    !showNewPassword
                  )
                }
                aria-label={
                  showNewPassword
                    ? "Hide new password"
                    : "Show new password"
                }
              >
                {showNewPassword ? (
                  <EyeOff size={20} />
                ) : (
                  <Eye size={20} />
                )}
              </button>
            </div>
          </div>

          {/* Confirm Password */}
          <div className="reset-password-field">
            <label htmlFor="confirm-password">
              Confirm New Password
            </label>

            <div className="password-input-wrapper">
              <input
                id="confirm-password"
                type={
                  showConfirmPassword
                    ? "text"
                    : "password"
                }
                placeholder="Confirm new password"
                value={confirmPassword}
                onChange={(event) => {
                  setConfirmPassword(
                    event.target.value
                  );
                  setError("");
                }}
                minLength={8}
                required
              />

              <button
                type="button"
                className="password-toggle"
                onClick={() =>
                  setShowConfirmPassword(
                    !showConfirmPassword
                  )
                }
                aria-label={
                  showConfirmPassword
                    ? "Hide confirm password"
                    : "Show confirm password"
                }
              >
                {showConfirmPassword ? (
                  <EyeOff size={20} />
                ) : (
                  <Eye size={20} />
                )}
              </button>
            </div>
          </div>

          {/* Error Message */}
          {error && (
            <p
              className="reset-password-error"
              role="alert"
            >
              {error}
            </p>
          )}

          {/* Submit Button */}
          <button
            type="submit"
            className="reset-password-button"
            disabled={loading}
          >
            {loading
              ? "Resetting Password..."
              : "Set New Password"}
          </button>
        </form>

        {/* Back to Login */}
        <div className="reset-password-footer">
          <span>Remember your password?</span>{" "}
          <Link to="/login">
            Back to Login
          </Link>
        </div>
      </motion.div>
    </div>
  );
}

export default ResetPasswordPage;