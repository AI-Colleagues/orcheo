import { useEffect, useState } from "react";
import { getSystemInfo } from "@/lib/api";

let _cached: boolean | null = null;

export function useUploadsAllowed(): boolean | null {
  const [uploadsAllowed, setUploadsAllowed] = useState<boolean | null>(_cached);

  useEffect(() => {
    if (_cached !== null) return;
    let active = true;
    getSystemInfo()
      .then((info) => {
        _cached = info.uploads_allowed;
        if (active) setUploadsAllowed(info.uploads_allowed);
      })
      .catch(() => {
        // Fail closed for security - default to disabling uploads on API error
        _cached = false;
        if (active) setUploadsAllowed(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return uploadsAllowed;
}
