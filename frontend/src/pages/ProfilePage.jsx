import { useOutletContext } from "react-router-dom";
import { CircleUserRound } from "lucide-react";

export default function ProfilePage() {
  const { user } = useOutletContext();

  const name = String(user.full_name || user.name || "Frontend User");
  const email = user.email || "user@example.com";
  const mobile = user.mobile_number || user.mobile || "Not provided";

  return (
    <section className="home-subpage">
      <span className="home-eyebrow">Account</span>
      <h2>Profile</h2>
      <p className="home-page-description">
        Your Convergence account information.
      </p>

      <div className="home-profile-card">
        <span className="home-profile-icon">
          <CircleUserRound size={39} aria-hidden="true" />
        </span>

        <div className="home-profile-field">
          <span>Name</span>
          <strong>{name}</strong>
        </div>

        <div className="home-profile-field">
          <span>Email</span>
          <strong>{email}</strong>
        </div>

        <div className="home-profile-field">
          <span>Mobile</span>
          <strong>{mobile}</strong>
        </div>
      </div>
    </section>
  );
}
