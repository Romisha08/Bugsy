export type ParsedRequirement = {
  line: number;
  original: string;
  name: string;
  specifier: string;
  extras: string[];
  marker: string | null;
};

export type ParseIssue = {
  line: number;
  original: string;
  reason: string;
};

export type ParseResponse = {
  source_id: string;
  parse_status: "parsed";
  python_version: string;
  requirements: ParsedRequirement[];
  warnings: string[];
  source_sha256: string;
};

export type ApiErrorBody = {
  code: string;
  message: string;
  details?: ParseIssue[] | Record<string, string> | null;
};
