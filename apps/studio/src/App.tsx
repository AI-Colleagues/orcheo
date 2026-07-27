import { useLayoutEffect } from "react";
import {
  BrowserRouter as Router,
  Navigate,
  Outlet,
  Route,
  Routes,
  useParams,
} from "react-router-dom";
import { Toaster } from "@/design-system/ui/toaster";
import { BrowserContextProvider } from "@/hooks/browser-context-provider";
import WorkflowGallery from "@features/workflow/pages/workflow-gallery";
import WorkflowPage from "@features/workflow/pages/workflow";
import Login from "@features/auth/pages/login";
import RequireAuth from "@features/auth/components/require-auth";
import AuthVerify from "@features/auth/pages/verify";
import Profile from "@features/account/pages/profile";
import Settings from "@features/account/pages/settings";
import WorkspaceManagement from "@features/account/pages/workspace-management";
import InvitationAccept from "@features/account/pages/invitation-accept";
import PublicChatPage from "@features/chatkit/pages/public-chat";
import {
  getSelectedWorkspaceSlug,
  setSelectedWorkspaceSlug,
} from "@/lib/workspace-session";
import { getWorkspaceGalleryPath } from "@/lib/workspace-routing";
import { WorkspaceBootstrapGate } from "@features/shared/components/workspace-bootstrap-gate";
import AppShell from "@features/shell/components/app-shell";
import Feedback from "@features/shell/pages/feedback";
import AppsList from "@features/apps/pages/apps-list";
import AppDetail from "@features/apps/pages/app-detail";
import AppAuthorize from "@features/apps/pages/app-authorize";

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
      <BrowserContextProvider>
        <Routes>
          <Route path="/login" element={<Login />} />

          <Route path="/auth/verify" element={<AuthVerify />} />
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
        <Toaster />
      </BrowserContextProvider>
    </Router>
  );
}
