import { useEffect, useState } from "react";
import { Copy, KeyRound, RotateCw, Trash2 } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import { Input } from "@/design-system/ui/input";
import { Label } from "@/design-system/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/design-system/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/design-system/ui/table";
import { Badge } from "@/design-system/ui/badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/design-system/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/design-system/ui/dialog";
import useCredentialVault from "@/hooks/use-credential-vault";
import { usePageContext } from "@/hooks/use-page-context";
import { toast } from "@/hooks/use-toast";
import TopNavigation from "@features/shared/components/top-navigation";
import {
  createServiceToken,
  getActiveWorkspace,
  listServiceTokens,
  revokeServiceToken,
  rotateServiceToken,
  type ServiceToken,
} from "@/lib/api";

const AVAILABLE_SCOPES = [
  "workflows:read",
  "workflows:write",
  "workflows:execute",
  "vault:read",
  "vault:write",
] as const;

const EXPIRY_OPTIONS = [
  { value: "never", label: "Never", seconds: null },
  { value: "1h", label: "1 hour", seconds: 3600 },
  { value: "1d", label: "1 day", seconds: 86400 },
  { value: "7d", label: "7 days", seconds: 604800 },
  { value: "30d", label: "30 days", seconds: 2592000 },
] as const;

const ROTATION_OVERLAP_SECONDS = 300;

const formatDate = (value: string | null | undefined): string =>
  value ? new Date(value).toLocaleString() : "—";

const tokenStatus = (token: ServiceToken): "Revoked" | "Rotated" | "Active" => {
  if (token.revoked_at) {
    return "Revoked";
  }
  if (token.rotated_to) {
    return "Rotated";
  }
  return "Active";
};

interface RevealedSecret {
  title: string;
  identifier: string;
  secret: string;
}

export default function ServiceTokens() {
  const { setPageContext } = usePageContext();
  useEffect(() => {
    setPageContext({ page: "workspace" });
  }, [setPageContext]);

  const {
    credentials,
    isLoading: isCredentialsLoading,
    onAddCredential,
    onUpdateCredential,
    onDeleteCredential,
    onRevealCredentialSecret,
  } = useCredentialVault();

  const [tokens, setTokens] = useState<ServiceToken[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [workspaceName, setWorkspaceName] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [identifier, setIdentifier] = useState("");
  const [selectedScopes, setSelectedScopes] = useState<Set<string>>(new Set());
  const [expiry, setExpiry] = useState<string>("never");
  const [isMinting, setIsMinting] = useState(false);

  const [revealed, setRevealed] = useState<RevealedSecret | null>(null);
  const [rotatingId, setRotatingId] = useState<string | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const loadData = async () => {
      try {
        const [workspace, tokenList] = await Promise.all([
          getActiveWorkspace(),
          listServiceTokens(),
        ]);
        if (!active) {
          return;
        }
        setWorkspaceName(workspace.name);
        setTokens(tokenList.tokens);
      } catch (err) {
        if (active) {
          toast({
            title: "Failed to load API keys",
            description: err instanceof Error ? err.message : undefined,
            variant: "destructive",
          });
        }
      } finally {
        if (active) {
          setIsLoading(false);
        }
      }
    };

    void loadData();

    return () => {
      active = false;
    };
  }, []);

  const resetCreateForm = () => {
    setIdentifier("");
    setSelectedScopes(new Set());
    setExpiry("never");
  };

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) => {
      const next = new Set(prev);
      if (next.has(scope)) {
        next.delete(scope);
      } else {
        next.add(scope);
      }
      return next;
    });
  };

  const handleMint = async () => {
    setIsMinting(true);
    try {
      const expiryOption = EXPIRY_OPTIONS.find(
        (option) => option.value === expiry,
      );
      const token = await createServiceToken({
        identifier: identifier.trim() || undefined,
        scopes: [...selectedScopes],
        expires_in_seconds: expiryOption?.seconds ?? undefined,
      });
      setTokens((prev) => [token, ...prev]);
      setRevealed({
        title: "API key created",
        identifier: token.identifier,
        secret: token.secret ?? "",
      });
      resetCreateForm();
      setCreateOpen(false);
      toast({ title: "API key created" });
    } catch (err) {
      toast({
        title: "Failed to create API key",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setIsMinting(false);
    }
  };

  const handleRotate = async (tokenId: string) => {
    setRotatingId(tokenId);
    try {
      const rotated = await rotateServiceToken(
        tokenId,
        ROTATION_OVERLAP_SECONDS,
      );
      const tokenList = await listServiceTokens();
      setTokens(tokenList.tokens);
      setRevealed({
        title: "API key rotated",
        identifier: rotated.identifier,
        secret: rotated.secret ?? "",
      });
      toast({ title: "API key rotated" });
    } catch (err) {
      toast({
        title: "Failed to rotate API key",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setRotatingId(null);
    }
  };

  const handleRevoke = async () => {
    const tokenId = revokeTarget;
    if (!tokenId) {
      return;
    }
    setRevokeTarget(null);
    setRevokingId(tokenId);
    try {
      await revokeServiceToken(tokenId, "Revoked via Canvas");
      setTokens((prev) => prev.filter((token) => token.identifier !== tokenId));
      toast({ title: "API key revoked" });
    } catch (err) {
      toast({
        title: "Failed to revoke API key",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setRevokingId(null);
    }
  };

  const handleCopySecret = async (secret: string) => {
    try {
      await navigator.clipboard.writeText(secret);
      toast({ title: "Secret copied to clipboard" });
    } catch {
      toast({
        title: "Could not copy secret",
        description: "Copy it manually from the field above.",
        variant: "destructive",
      });
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <TopNavigation
        credentials={credentials}
        isCredentialsLoading={isCredentialsLoading}
        onAddCredential={onAddCredential}
        onUpdateCredential={onUpdateCredential}
        onDeleteCredential={onDeleteCredential}
        onRevealCredentialSecret={onRevealCredentialSecret}
      />

      <main className="flex-1 min-h-0 overflow-auto">
        <div className="mx-auto flex w-full max-w-7xl flex-col space-y-6 p-8 pt-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-3xl font-bold tracking-tight">API Keys</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                API keys created here are scoped to{" "}
                <span className="font-medium text-foreground">
                  {workspaceName ?? "this workspace"}
                </span>{" "}
                and cannot access any other workspace.
              </p>
            </div>
            <Button onClick={() => setCreateOpen(true)}>
              <KeyRound className="mr-2 h-4 w-4" />
              Create a new key
            </Button>
          </div>

          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading API keys...</p>
          ) : tokens.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No API keys for this workspace yet.
            </p>
          ) : (
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Identifier</TableHead>
                    <TableHead>Key</TableHead>
                    <TableHead>Scopes</TableHead>
                    <TableHead>Issued</TableHead>
                    <TableHead>Expires</TableHead>
                    <TableHead>Last used</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tokens.map((token) => {
                    const status = tokenStatus(token);
                    const isActive = status === "Active";
                    return (
                      <TableRow key={token.identifier}>
                        <TableCell className="font-mono text-sm">
                          {token.identifier}
                        </TableCell>
                        <TableCell className="font-mono text-sm text-muted-foreground">
                          {token.secret_preview
                            ? `••••••••${token.secret_preview}`
                            : "—"}
                        </TableCell>
                        <TableCell className="text-sm">
                          {token.scopes.length > 0 ? (
                            <div className="flex flex-wrap gap-1">
                              {token.scopes.map((scope) => (
                                <Badge key={scope} variant="secondary">
                                  {scope}
                                </Badge>
                              ))}
                            </div>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {formatDate(token.issued_at)}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {token.expires_at
                            ? formatDate(token.expires_at)
                            : "Never"}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {formatDate(token.last_used_at)}
                        </TableCell>
                        <TableCell>
                          <Badge variant={isActive ? "default" : "outline"}>
                            {status}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                void handleRotate(token.identifier)
                              }
                              disabled={
                                !isActive ||
                                rotatingId === token.identifier ||
                                revokingId === token.identifier
                              }
                            >
                              <RotateCw className="mr-2 h-4 w-4" />
                              {rotatingId === token.identifier
                                ? "Rotating..."
                                : "Rotate"}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-destructive hover:text-destructive"
                              onClick={() => setRevokeTarget(token.identifier)}
                              disabled={
                                !isActive ||
                                rotatingId === token.identifier ||
                                revokingId === token.identifier
                              }
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              {revokingId === token.identifier
                                ? "Revoking..."
                                : "Revoke"}
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      </main>

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          if (!isMinting) {
            setCreateOpen(open);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create API key</DialogTitle>
            <DialogDescription>
              The key is scoped to this workspace. Its secret is shown once
              after creation.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="token-identifier">Identifier (optional)</Label>
              <Input
                id="token-identifier"
                placeholder="Auto-generated if left blank"
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value)}
                disabled={isMinting}
              />
            </div>

            <div className="space-y-1.5">
              <Label>Scopes</Label>
              <div className="flex flex-wrap gap-x-4 gap-y-2">
                {AVAILABLE_SCOPES.map((scope) => (
                  <label
                    key={scope}
                    className="flex items-center gap-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-border"
                      checked={selectedScopes.has(scope)}
                      onChange={() => toggleScope(scope)}
                      disabled={isMinting}
                    />
                    <span className="font-mono">{scope}</span>
                  </label>
                ))}
              </div>
            </div>

            <div className="w-44 space-y-1.5">
              <Label htmlFor="token-expiry">Expires</Label>
              <Select
                value={expiry}
                onValueChange={setExpiry}
                disabled={isMinting}
              >
                <SelectTrigger id="token-expiry">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EXPIRY_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => void handleMint()} disabled={isMinting}>
              {isMinting ? "Creating..." : "Create a new key"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {revealed && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) {
              setRevealed(null);
            }
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{revealed.title}</DialogTitle>
              <DialogDescription>
                Copy this secret now — it will not be shown again.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <p className="text-sm">
                <span className="text-muted-foreground">ID: </span>
                <span className="font-mono">{revealed.identifier}</span>
              </p>
              <p className="break-all rounded border bg-muted p-3 font-mono text-sm">
                {revealed.secret}
              </p>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => void handleCopySecret(revealed.secret)}
              >
                <Copy className="mr-2 h-4 w-4" />
                Copy secret
              </Button>
              <Button onClick={() => setRevealed(null)}>Done</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      <AlertDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRevokeTarget(null);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke API key</AlertDialogTitle>
            <AlertDialogDescription>
              Revoking <span className="font-mono">{revokeTarget}</span>{" "}
              immediately disables it. Any client using this key will lose
              access. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => void handleRevoke()}
            >
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
