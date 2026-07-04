import { type CookieOptions } from "@supabase/ssr";

export type CookieToSet = {
  name: string;
  value: string;
  options?: CookieOptions;
};
