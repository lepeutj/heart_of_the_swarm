function asObject(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function exampleValue(name: string, schema: Record<string, unknown>): unknown {
  if ("default" in schema) return schema.default;
  switch (schema.type) {
    case "string":
      return name === "request" ? "Enter your request" : "";
    case "integer":
    case "number":
      return 0;
    case "boolean":
      return false;
    case "array":
      return [];
    case "object":
      return buildRunInput(schema);
    default:
      return null;
  }
}

export function buildRunInput(schema: unknown): Record<string, unknown> {
  const objectSchema = asObject(schema);
  const properties = asObject(objectSchema.properties);
  const required = Array.isArray(objectSchema.required)
    ? objectSchema.required.filter((name): name is string => typeof name === "string")
    : [];
  return Object.fromEntries(required.map((name) => [
    name,
    exampleValue(name, asObject(properties[name])),
  ]));
}
