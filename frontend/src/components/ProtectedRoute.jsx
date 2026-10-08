import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";

import {
  UNAUTHORIZED_EVENT,
  getAccessToken,
} from "../services/authedFetch.js";

/*
 * Single place that guards authenticated routes.
 * authedFetch clears the session and fires UNAUTHORIZED_EVENT on any 401,
 * which sends the user back to /login from here.
 */
export default function ProtectedRoute() {
  const [authenticated, setAuthenticated] = useState(
    () => Boolean(getAccessToken())
  );

  useEffect(() => {
    const onUnauthorized = () => setAuthenticated(false);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () =>
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  if (!authenticated || !getAccessToken()) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
