export type Stage = "data" | "workspace" | "review" | "export";

export type Manifest = {
  manifest_id: string;
  name: string;
  source_type: string;
  created_at: string;
  item_count: number;
};

export type RemoteDataset = {
  dataset_id: string;
  name: string;
  item_count: number;
};

export type IwiscTenant = {
  tenant_id: string;
  system_name: string;
  server_host_name: string;
  target_format: string;
  has_api_token: boolean;
  result_delivery_configured?: boolean;
};

export type IwiscTask = {
  ant_id: string;
  ant_active: number;
  ant_period: string;
  external_context: Record<string, string>;
};

export type LinkedIwiscTask = {
  task_id: string;
  ant_id: string;
  ant_active: number;
  annotated_by: string;
  delivery_status: string;
};

export type Shape = {
  label: string;
  shape_type: string;
  points: number[][];
  score?: number | null;
  group_id?: number | null;
  description?: string;
  difficult?: boolean;
  flags?: Record<string, unknown>;
  [key: string]: unknown;
};

export type Annotation = {
  version?: string;
  flags?: Record<string, unknown>;
  shapes: Shape[];
  imagePath?: string;
  imageData?: string | null;
  imageHeight?: number;
  imageWidth?: number;
  [key: string]: unknown;
};

export type Item = {
  item_id: string;
  name: string;
  width?: number | null;
  height?: number | null;
  image_url: string;
  has_annotation?: boolean;
  has_bbox?: boolean;
  has_classification?: boolean;
  classification?: string;
  max_conf?: number;
  shape_count?: number;
  labels?: string[];
  review_status?: "pending" | "approved" | "rejected";
  review_comment?: string;
  shapes?: Shape[];
};

export type HandoffReviewPair = {
  left_item_id: string;
  right_item_id: string;
  left_filename: string;
  right_filename: string;
  left_split: string;
  right_split: string;
  distance: number;
};

export type HandoffReviewContext = {
  handoff_id: string;
  task: "verify";
  kind: "duplicate_pair_review";
  method: "phash" | "embedding";
  cross_split_only: boolean;
  instructions: string;
  pairs: HandoffReviewPair[];
};

export type Workspace = {
  manifest_id: string;
  manifest_name: string;
  total: number;
  annotated: number;
  pending: number;
  labels: string[];
  classification_labels: string[];
  annotation_tool: string;
  iwisc_task?: LinkedIwiscTask | null;
  handoff_context?: HandoffReviewContext | null;
  items: Item[];
};

export type LabelAnalysis = {
  manifest_id: string;
  label_map: Record<string, number>;
  label_counts: Record<string, number>;
  near_dupes: string[][];
  classification_counts: Record<string, number>;
  total_items: number;
  annotated_xany: number;
  export_count: number;
};

export type ExportResult = {
  mode: string;
  error?: string | null;
  total_items?: number;
  annotated_items?: number;
  classified_items?: number;
  validation_issues?: Array<{ severity: "error" | "warning" | "info" | string; code: string; item_id: string; message: string }>;
  exports?: Record<string, string>;
  export_count?: number;
};
