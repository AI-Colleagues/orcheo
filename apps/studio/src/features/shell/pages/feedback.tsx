import { useEffect } from "react";
import { Github, ExternalLink } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import { usePageContext } from "@/hooks/use-page-context";
import { ORCHEO_ISSUE_CHOOSER_URL } from "@features/shell/constants";

export default function Feedback() {
  const { setPageContext } = usePageContext();
  useEffect(() => {
    setPageContext({ page: "other" });
  }, [setPageContext]);

  return (
    <main className="flex h-full min-h-0 items-center justify-center overflow-auto p-8">
      <div className="flex max-w-md flex-col items-center text-center">
        <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Github className="h-7 w-7" />
        </span>
        <h1 className="text-xl font-semibold text-foreground">
          Feedback &amp; issues
        </h1>
        <p className="mt-2 mb-5 text-sm text-muted-foreground">
          Found a bug or have an idea for your AI colleagues? Open an issue on
          GitHub and the Orcheo team will take a look.
        </p>
        <Button asChild>
          <a href={ORCHEO_ISSUE_CHOOSER_URL} target="_blank" rel="noreferrer">
            <Github className="mr-2 h-4 w-4" />
            Open GitHub issues
            <ExternalLink className="ml-2 h-3.5 w-3.5" />
          </a>
        </Button>
      </div>
    </main>
  );
}
