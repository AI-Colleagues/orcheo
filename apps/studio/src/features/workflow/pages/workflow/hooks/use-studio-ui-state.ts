import { useState } from "react";

export function useStudioUiState() {
  const [activeTab, setActiveTab] = useState("workflow");

  return {
    activeTab,
    setActiveTab,
  };
}
