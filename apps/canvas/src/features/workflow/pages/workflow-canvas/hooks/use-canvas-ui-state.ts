import { useState } from "react";

export function useCanvasUiState() {
  const [activeTab, setActiveTab] = useState("workflow");

  return {
    activeTab,
    setActiveTab,
  };
}
