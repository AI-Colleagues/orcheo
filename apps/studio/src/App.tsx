import { lazy, Suspense, useLayoutEffect } from "react";
import {
  BrowserRouter as Router,
  Navigate,
  Outlet,
  Route,
  Routes,
  useParams,
} from "react-router-dom";
import { Toaster } from "@/design-system/ui/toaster";
import RequireAuth from "@features/auth/components/require-auth";
import {
  getSelectedWorkspaceSlug,
  setSelectedWorkspaceSlug,
} from "@/lib/workspace-session";
import { getWorkspaceGalleryPath } from "@/lib/workspace-routing";
import { WorkspaceBootstrapGate } from "@features/shared/components/workspace-bootstrap-gate";
import AppShell from "@features/shell/components/app-shell";

const WorkflowGallery = lazy(
  () => import("@features/workflow/pages/workflow-gallery"),
);
const WorkflowPage = lazy(() => import("@features/workflow/pages/workflow"));
const Login = lazy(() => import("@features/auth/pages/login"));
const OAuthConsent = lazy(() => import("@features/auth/pages/oauth-consent"));
const Profile = lazy(() => import("@features/account/pages/profile"));
const Settings = lazy(() => import("@features/account/pages/settings"));
const WorkspaceManagement = lazy(
  () => import("@features/account/pages/workspace-management"),
);
const InvitationAccept = lazy(
  () => import("@features/account/pages/invitation-accept"),
);
const PublicChatPage = lazy(() => import("@features/chatkit/pages/public-chat"));
const Feedback = lazy(() => import("@features/shell/pages/feedback"));
const AppsList = lazy(() => import("@features/apps/pages/apps-list"));
const AppDetail = lazy(() => import("@features/apps/pages/app-detail"));
const AppAuthorize = lazy(() => import("@features/apps/pages/app-authorize"));

const syncWorkspaceSlug = (workspaceSlug?: string) => {
  if (!workspaceSlug) {
    return;
  }
  setSelectedWorkspaceSlug(workspaceSlug);
};

function WorkspaceHomeRedirect() {
  const workspaceSlug = getSelectedWorkspaceSlug();
  if (!workspaceSlug) {
    return <WorkflowGallery />;
  }
  return <Navigate to={getWorkspaceGalleryPath(workspaceSlug)} replace />;
}

function WorkspaceGalleryRoute() {
  const { workspaceSlug } = useParams<{ workspaceSlug?: string }>();
  useLayoutEffect(() => {
    syncWorkspaceSlug(workspaceSlug);
  }, [workspaceSlug]);

  return <WorkflowGallery />;
}

function WorkspaceManagementRoute() {
  const { workspaceSlug } = useParams<{ workspaceSlug?: string }>();
  useLayoutEffect(() => {
    syncWorkspaceSlug(workspaceSlug);
  }, [workspaceSlug]);

  return <WorkspaceManagement />;
}

function RequireWorkspace() {
  return (
    <WorkspaceBootstrapGate>
      <Outlet />
    </WorkspaceBootstrapGate>
  );
}

function AppShellLayout() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}

function WorkspaceAppsRoute() {
  const { workspaceSlug } = useParams<{ workspaceSlug?: string }>();
  useLayoutEffect(() => {
    syncWorkspaceSlug(workspaceSlug);
  }, [workspaceSlug]);

  return <AppsList />;
}

function WorkspaceAppDetailRoute() {
  const { workspaceSlug } = useParams<{ workspaceSlug?: string }>();
  useLayoutEffect(() => {
    syncWorkspaceSlug(workspaceSlug);
  }, [workspaceSlug]);

  return <AppDetail />;
}

function WorkspaceWorkflowRoute() {
  const { workspaceSlug, workflowId } = useParams<{
    workspaceSlug?: string;
    teamSlug?: string;
    workflowId?: string;
  }>();
  useLayoutEffect(() => {
    syncWorkspaceSlug(workspaceSlug);
  }, [workspaceSlug]);

  return (
    <WorkflowPage workflowId={workflowId === "new" ? undefined : workflowId} />
  );
}

export default function OrcheoStudioApp() {
  return (
    <Router>
      <>
        <Suspense
          fallback={
            <div role="status" className="p-6">
              Loading…
            </div>
          }
        >
          <Routes>
            <Route path="/login" element={<Login />} />

            <Route path="/chat/:workflowId" element={<PublicChatPage />} />
            <Route
              path="/chat/team/:teamSlug/:workflowId"
              element={<PublicChatPage />}
            />
            <Route
              path="/chat/:workspaceSlug/:workflowId"
              element={<PublicChatPage />}
            />
            <Route
              path="/chat/:workspaceSlug/team/:teamSlug/:workflowId"
              element={<PublicChatPage />}
            />

            <Route element={<RequireAuth />}>
              <Route path="/invitations/accept" element={<InvitationAccept />} />
              <Route path="/apps/authorize" element={<AppAuthorize />} />
              <Route path="/oauth/consent" element={<OAuthConsent />} />
              <Route element={<RequireWorkspace />}>
                <Route element={<AppShellLayout />}>
                  <Route path="/" element={<WorkspaceHomeRedirect />} />
                  <Route
                    path="/:workspaceSlug"
                    element={<WorkspaceGalleryRoute />}
                  />

                  <Route
                    path="/:workspaceSlug/apps"
                    element={<WorkspaceAppsRoute />}
                  />
                  <Route
                    path="/:workspaceSlug/apps/:appId"
                    element={<WorkspaceAppDetailRoute />}
                  />

                  <Route
                    path="/:workspaceSlug/workspace"
                    element={<WorkspaceManagementRoute />}
                  />

                  <Route
                    path="/:workspaceSlug/new"
                    element={<WorkspaceWorkflowRoute />}
                  />
                  <Route
                    path="/:workspaceSlug/team/:teamSlug/:workflowId"
                    element={<WorkspaceWorkflowRoute />}
                  />
                  <Route
                    path="/:workspaceSlug/:workflowId"
                    element={<WorkspaceWorkflowRoute />}
                  />

                  <Route path="/profile" element={<Profile />} />

                  <Route path="/settings" element={<Settings />} />

                  <Route path="/feedback" element={<Feedback />} />
                </Route>
              </Route>
            </Route>
          </Routes>
        </Suspense>
        <Toaster />
      </>
    </Router>
  );
}
