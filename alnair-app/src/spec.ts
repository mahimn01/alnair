import { z } from "zod";
const text = z.string().max(32000),
  short = z.string().max(240),
  id = z.string().regex(/^[a-zA-Z0-9_-]{1,80}$/);
const base = { id };
export const componentSchema = z.discriminatedUnion("type", [
  z.strictObject({
    ...base,
    type: z.literal("card"),
    title: short,
    body: text,
    metric: short.optional(),
    badge: short.optional(),
  }),
  z.strictObject({
    ...base,
    type: z.literal("table"),
    title: short,
    columns: z.array(short).min(1).max(20),
    rows: z.array(z.array(short).max(20)).max(500),
  }),
  z.strictObject({
    ...base,
    type: z.literal("chart"),
    title: short,
    kind: z.enum(["line", "bar"]),
    points: z
      .array(
        z.strictObject({
          label: short,
          value: z.number().min(-1e12).max(1e12),
        }),
      )
      .min(1)
      .max(200),
  }),
  z.strictObject({
    ...base,
    type: z.literal("diff"),
    title: short,
    before: text,
    after: text,
  }),
  z.strictObject({
    ...base,
    type: z.literal("code"),
    title: short,
    text,
    language: short.optional(),
  }),
  z.strictObject({
    ...base,
    type: z.literal("checklist"),
    title: short,
    items: z
      .array(z.strictObject({ label: short, done: z.boolean() }))
      .max(100),
    progress: z.number().min(0).max(100).optional(),
  }),
  z.strictObject({
    ...base,
    type: z.literal("form"),
    title: short,
    fields: z
      .array(
        z.strictObject({
          name: id,
          label: short,
          kind: z.enum(["text", "textarea", "select"]).optional(),
          required: z.boolean().optional(),
          options: z.array(short).max(40).optional(),
        }),
      )
      .min(1)
      .max(20),
  }),
  z.strictObject({
    ...base,
    type: z.literal("choices"),
    title: short,
    options: z.array(short).min(1).max(20),
  }),
  z.strictObject({ ...base, type: z.literal("markdown"), text }),
  z.strictObject({
    ...base,
    type: z.literal("image"),
    src: z
      .string()
      .max(1000000)
      .regex(/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/),
    alt: short,
    caption: short.optional(),
  }),
]);
export type Spec = z.infer<typeof componentSchema>;
export function validate(value: unknown): Spec {
  return componentSchema.parse(value);
}
