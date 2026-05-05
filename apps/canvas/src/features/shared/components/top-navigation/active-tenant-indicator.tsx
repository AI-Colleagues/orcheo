import { useEffect, useState } from "react";

import { Badge } from "@/design-system/ui/badge";
import { getActiveTenant, type ActiveTenantResponse } from "@/lib/api";

export default function ActiveTenantIndicator() {
  const [tenant, setTenant] = useState<ActiveTenantResponse | null>(null);

  useEffect(() => {
    let active = true;

    void getActiveTenant()
      .then((payload) => {
        if (active) {
          setTenant(payload);
        }
      })
      .catch(() => {
        // Leave the header uncluttered when the backend cannot resolve tenant state.
      });

    return () => {
      active = false;
    };
  }, []);

  if (!tenant) {
    return null;
  }

  return (
    <Badge
      variant="outline"
      className="hidden items-center gap-1 border-dashed bg-background/80 text-foreground sm:inline-flex"
      data-testid="active-tenant-indicator"
    >
      <span className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
        Tenant
      </span>
      <span className="max-w-[10rem] truncate font-medium">{tenant.slug}</span>
    </Badge>
  );
}
