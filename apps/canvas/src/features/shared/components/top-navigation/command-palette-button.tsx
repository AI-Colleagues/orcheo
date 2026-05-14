import React, { useState } from "react";
import { Button } from "@/design-system/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/design-system/ui/dialog";
import { Input } from "@/design-system/ui/input";
import { Badge } from "@/design-system/ui/badge";
import { Command, Folder, Search } from "lucide-react";

export default function CommandPaletteButton() {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          className="hidden items-center gap-1 sm:flex"
          onClick={() => setIsOpen(true)}
        >
          <Search className="mr-1 h-4 w-4" />
          <span>Search</span>
          <kbd className="pointer-events-none ml-auto inline-flex h-5 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
            <span className="text-xs">⌘</span>K
          </kbd>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[550px]">
        <DialogHeader>
          <DialogTitle>Search</DialogTitle>
          <DialogDescription>
            Search for colleagues, nodes, or actions
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center border-b py-2">
          <Search className="mr-2 h-4 w-4 shrink-0 opacity-50" />
          <Input
            className="flex h-10 w-full rounded-md border-0 bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
            placeholder="Type to search..."
            autoFocus
          />
        </div>
        <div className="mt-2 space-y-1">
          <p className="text-xs text-muted-foreground">Recent searches</p>
          <div className="grid gap-1">
            <Button
              variant="ghost"
              className="justify-start text-sm"
              onClick={() => setIsOpen(false)}
            >
              <Folder className="mr-2 h-4 w-4" />
              <span>Customer Onboarding</span>
              <Badge variant="outline" className="ml-auto">
                Workflow
              </Badge>
            </Button>
            <Button
              variant="ghost"
              className="justify-start text-sm"
              onClick={() => setIsOpen(false)}
            >
              <Command className="mr-2 h-4 w-4" />
              <span>HTTP Request</span>
              <Badge variant="outline" className="ml-auto">
                Node
              </Badge>
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
