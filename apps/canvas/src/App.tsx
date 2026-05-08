import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import { Toaster } from "@/design-system/ui/toaster";
import { BrowserContextProvider } from "@/hooks/browser-context-provider";
import { VibeProvider } from "@features/vibe/context/vibe-provider";
import { VibeAuthenticatedLayout } from "@features/vibe/components/vibe-authenticated-layout";
import WorkflowGallery from "@features/workflow/pages/workflow-gallery";
import WorkflowCanvas from "@features/workflow/pages/workflow-canvas";
import WorkflowRemediations from "@features/workflow/pages/workflow-remediations";
import Login from "@features/auth/pages/login";
import RequireAuth from "@features/auth/components/require-auth";
import OAuthCallback from "@features/auth/pages/oauth-callback";
import Profile from "@features/account/pages/profile";
import Settings from "@features/account/pages/settings";
import HelpSupport from "@features/support/pages/help-support";
import PublicChatPage from "@features/chatkit/pages/public-chat";

export default function OrcheoCanvasApp() {
  return (
    <Router>
      <BrowserContextProvider>
        <VibeProvider>
          <Routes>
            <Route path="/login" element={<Login />} />

            <Route path="/auth/callback" element={<OAuthCallback />} />
            <Route path="/chat/:workflowId" element={<PublicChatPage />} />

            <Route element={<RequireAuth />}>
              <Route element={<VibeAuthenticatedLayout />}>
                <Route path="/" element={<WorkflowGallery />} />

                <Route path="/workflow-canvas" element={<WorkflowCanvas />} />
                <Route
                  path="/workflow-canvas/:workflowId"
                  element={<WorkflowCanvas />}
                />

                <Route
                  path="/workflow-remediations"
                  element={<WorkflowRemediations />}
                />

                <Route path="/profile" element={<Profile />} />

                <Route path="/settings" element={<Settings />} />

                <Route path="/help-support" element={<HelpSupport />} />
              </Route>
            </Route>
          </Routes>
          <Toaster />
        </VibeProvider>
      </BrowserContextProvider>
    </Router>
  );
}
