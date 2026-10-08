import { Link } from "react-router-dom";
import { motion } from "motion/react";
import { Check } from "lucide-react";


function PasswordResetSuccessPage() {
  // Frontend-only success screen. Connect the real reset API later.
  return (
    <main className="password-reset-success-page">
      <motion.div
        className="password-reset-success-card"
        initial={{ opacity: 0, y: 20, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
      >
        <div className="password-reset-success-icon">
          <Check
            size={42}
            strokeWidth={2.5}
            aria-hidden="true"
          />
        </div>

        <h1 className="password-reset-success-title">
          Password Reset Successful!
        </h1>

        <p className="password-reset-success-description">
          Your password has been changed successfully.
          You can now log in using your new password.
        </p>

        <Link
          to="/login"
          className="password-reset-success-button"
        >
          Back to Login
        </Link>
      </motion.div>
    </main>
  );
}

export default PasswordResetSuccessPage;