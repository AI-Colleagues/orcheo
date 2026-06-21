import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import { acceptWorkspaceInvitation } from "@/lib/api";
import { setSelectedWorkspaceSlug } from "@/lib/workspace-session";
import { getWorkspaceGalleryPath } from "@/lib/workspace-routing";

type AcceptState = "working" | "success" | "error";

export default function InvitationAccept() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get("token");
  const [state, setState] = useState<AcceptState>("working");
  const [message, setMessage] = useState<string>("");
  const [workspaceName, setWorkspaceName] = useState<string>("");
  // Guard against the effect running twice (React 18 StrictMode) redeeming
  // the single-use token twice.
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) {
      return;
    }
    startedRef.current = true;

    if (!token) {
      setState("error");
      setMessage("This invitation link is missing its token.");
      return;
    }

    let active = true;
    void (async () => {
      try {
        const result = await acceptWorkspaceInvitation(token);
        if (!active) {
          return;
        }
        setWorkspaceName(result.name);
        setSelectedWorkspaceSlug(result.slug);
        setState("success");
        navigate(getWorkspaceGalleryPath(result.slug), { replace: true });
      } catch (err) {
        if (!active) {
          return;
        }
        setState("error");
        setMessage(
          err instanceof Error
            ? err.message
            : "We couldn't accept this invitation.",
        );
      }
    })();

    return () => {
      active = false;
    };
  }, [token, navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-md rounded-lg border p-6 text-center">
        {state === "working" && (
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              Accepting your invitation…
            </p>
          </div>
        )}

        {state === "success" && (
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {workspaceName
                ? `Welcome to ${workspaceName}. Redirecting…`
                : "Invitation accepted. Redirecting…"}
            </p>
          </div>
        )}

        {state === "error" && (
          <div className="flex flex-col items-center gap-4">
            <h2 className="text-lg font-medium">Invitation unavailable</h2>
            <p className="text-sm text-muted-foreground">{message}</p>
            <Button onClick={() => navigate("/", { replace: true })}>
              Go to your workspaces
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
