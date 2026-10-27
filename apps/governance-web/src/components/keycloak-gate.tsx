"use client";

import { Clapperboard } from "lucide-react";
import Keycloak from "keycloak-js";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

function SignInGate({ onLogin, error }: { onLogin: () => void; error: string }) {
  return (
    <main className="auth-gate">
      <section className="auth-card">
        <span className="brand-mark" aria-hidden="true">
          <Clapperboard size={22} strokeWidth={1.8} />
        </span>
        <span className="panel-eyebrow">UNIVERSAL VIDEO MAKER</span>
        <h1>Sign in to your workspace</h1>
        <p>Records are protected. Sign in with your Keycloak account to continue.</p>
        {error && <p role="alert">{error}</p>}
        <Button className="approve-button" onClick={onLogin} type="button">
          {error ? "Retry connection" : "Sign in"}
        </Button>
      </section>
    </main>
  );
}

let keycloakInstance: Keycloak | null = null;
let keycloakInit: Promise<boolean> | null = null;

export function KeycloakGate({ children }: { children: (keycloak: Keycloak) => ReactNode }) {
  const [keycloak, setKeycloak] = useState<Keycloak | null>(null);
  const [initialized, setInitialized] = useState(false);
  const [authError, setAuthError] = useState("");

  useEffect(() => {
    let active = true;
    keycloakInstance ??= new Keycloak({
      url: process.env.NEXT_PUBLIC_KEYCLOAK_URL ?? "http://localhost:8080",
      realm: process.env.NEXT_PUBLIC_KEYCLOAK_REALM ?? "universal-video-maker",
      clientId: process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT_ID ?? "universal-video-maker-web",
    });
    const client = keycloakInstance;

    // Init runs once per page load (also under React strict mode) and never redirects;
    // it only processes the callback after a user-initiated login.
    keycloakInit ??= client.init({ pkceMethod: "S256", checkLoginIframe: false });
    keycloakInit
      .then(() => {
        if (!active) return;
        setKeycloak(client);
        setInitialized(true);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setAuthError(
          error instanceof Error
            ? `Could not connect to Keycloak: ${error.message}`
            : "Could not connect to Keycloak.",
        );
        setInitialized(true);
      });

    return () => {
      active = false;
    };
  }, []);

  if (!initialized) {
    return <main className="auth-gate" role="status">Connecting to Keycloak...</main>;
  }
  if (!keycloak) {
    return <SignInGate error={authError} onLogin={() => window.location.reload()} />;
  }
  if (!keycloak.authenticated) {
    return (
      <SignInGate
        error={authError}
        onLogin={() => {
          void keycloak
            .login({ redirectUri: window.location.href })
            .catch((error: unknown) => {
              setAuthError(
                error instanceof Error
                  ? `Could not start sign-in: ${error.message}`
                  : "Could not start sign-in.",
              );
            });
        }}
      />
    );
  }
  return <>{children(keycloak)}</>;
}
