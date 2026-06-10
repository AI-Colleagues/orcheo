import { Component, type ErrorInfo, type ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/design-system/ui/alert";
import { Button } from "@/design-system/ui/button";

interface PublicChatErrorBoundaryProps {
  children: ReactNode;
  onReset?: () => void;
}

interface PublicChatErrorBoundaryState {
  hasError: boolean;
  errorMessage?: string;
}

export class PublicChatErrorBoundary extends Component<
  PublicChatErrorBoundaryProps,
  PublicChatErrorBoundaryState
> {
  public state: PublicChatErrorBoundaryState = {
    hasError: false,
    errorMessage: undefined,
  };

  public static getDerivedStateFromError(
    error: Error,
  ): PublicChatErrorBoundaryState {
    return {
      hasError: true,
      errorMessage: error.message,
    };
  }

  public componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("PublicChatErrorBoundary caught an error", error, info);
  }

  private handleReset = () => {
    this.setState({ hasError: false, errorMessage: undefined });
    this.props.onReset?.();
  };

  public override render(): ReactNode {
    if (this.state.hasError) {
      return (
        <Alert className="rounded-3xl border-red-500/40 bg-red-500/10 p-6 text-red-100">
          <AlertTitle>Something went wrong</AlertTitle>
          <AlertDescription>
            {this.state.errorMessage ||
              "The public chat encountered an unexpected error. Please try again."}
          </AlertDescription>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button variant="outline" onClick={this.handleReset}>
              Try again
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                if (typeof window !== "undefined") {
                  window.location.reload();
                }
              }}
            >
              Reload page
            </Button>
          </div>
        </Alert>
      );
    }

    return this.props.children;
  }
}

export default PublicChatErrorBoundary;
