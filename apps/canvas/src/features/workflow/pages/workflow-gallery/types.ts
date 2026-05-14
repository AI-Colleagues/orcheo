export type WorkflowGalleryTab = "all" | "pinned" | "templates";

export type WorkflowGallerySort = "updated" | "created" | "name";

export type WorkflowGalleryTabCounts = Record<WorkflowGalleryTab, number>;

export interface WorkflowGalleryFilters {
  owner: {
    me: boolean;
    shared: boolean;
  };
  status: {
    active: boolean;
    draft: boolean;
    archived: boolean;
  };
  tags: {
    favorite: boolean;
    template: boolean;
    production: boolean;
    development: boolean;
  };
}
