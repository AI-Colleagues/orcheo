import { Button } from "@/design-system/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/design-system/ui/dialog";
import { type ApiTeam } from "@features/workflow/lib/workflow-storage-api";

interface OnboardTeamDialogProps {
  open: boolean;
  candidateName: string;
  teams: ApiTeam[];
  onSelect: (teamId: string) => void;
  onOpenChange: (open: boolean) => void;
}

/**
 * Prompts which team to onboard a candidate into when the workspace has more
 * than one team.
 */
export const OnboardTeamDialog = ({
  open,
  candidateName,
  teams,
  onSelect,
  onOpenChange,
}: OnboardTeamDialogProps) => {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Choose a team</DialogTitle>
          <DialogDescription>
            Select the team to onboard "{candidateName}" into.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          {teams.map((team) => (
            <Button
              key={team.id}
              variant="outline"
              className="justify-between"
              onClick={() => onSelect(team.id)}
            >
              <span>{team.name}</span>
              {team.is_default ? (
                <span className="text-xs text-muted-foreground">Default</span>
              ) : null}
            </Button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
};
