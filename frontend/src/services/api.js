const API_BASE = import.meta.env.VITE_API_BASE_URL;

async function parseResponse(response, action) {
  const text = await response.text();

  if (!text.trim()) {
    throw new Error(
      `${action} failed: the backend returned an empty response. Make sure the FastAPI backend is running.`
    );
  }

  let data;

  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(
      `${action} failed: the backend returned an invalid response. Make sure the FastAPI backend is running.`
    );
  }

  if (!response.ok) {
    const detail = data?.detail;

    throw new Error(
      typeof detail === "string"
        ? detail
        : `${action} failed (HTTP ${response.status}).`
    );
  }

  return data;
}


// ============================================================
// AUTHENTICATION
// ============================================================

// REGISTER
export async function registerUser(data) {
  return parseResponse(
    await fetch(
      `${API_BASE}/auth/register`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(data),
      }
    ),
    "Registration"
  );
}


// LOGIN
export async function loginUser(
  email,
  password,
  signal
) {
  return parseResponse(
    await fetch(
      `${API_BASE}/auth/login`,
      {
        signal,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email,
          password,
        }),
      }
    ),
    "Login"
  );
}


// FORGOT PASSWORD
export async function forgotPassword(
  email
) {
  return parseResponse(
    await fetch(
      `${API_BASE}/auth/forgot-password?email=${encodeURIComponent(email)}`,
      {
        method: "POST",
      }
    ),
    "Forgot password"
  );
}


// RESET PASSWORD
export async function resetPassword(
  token,
  newPassword,
  confirmPassword
) {
  return parseResponse(
    await fetch(
      `${API_BASE}/auth/reset-password`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          token,
          new_password: newPassword,
          confirm_password: confirmPassword,
        }),
      }
    ),
    "Reset password"
  );
}