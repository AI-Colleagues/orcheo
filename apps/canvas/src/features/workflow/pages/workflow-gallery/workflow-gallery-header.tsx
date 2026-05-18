import { useState } from "react";
import { Upload } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import { UploadWorkflowDialog } from "@features/workflow/components/dialogs/upload-workflow-dialog";

export const WorkflowGalleryHeader = () => {
  const [isUploadOpen, setIsUploadOpen] = useState(false);

  return (
    <div className="flex items-center justify-end gap-2 px-4 py-2">
      <Button
        variant="outline"
        size="sm"
        onClick={() => setIsUploadOpen(true)}
      >
        <Upload className="mr-2 h-4 w-4" />
        Upload
      </Button>
      <UploadWorkflowDialog
        open={isUploadOpen}
        onOpenChange={setIsUploadOpen}
      />
    </div>
  );
};
