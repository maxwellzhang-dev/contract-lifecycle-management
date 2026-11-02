import { type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "outline" | "ghost" | "destructive" };
export function Button({ className, variant = "default", ...props }: Props) {
  const variants = { default: "bg-primary text-primary-foreground hover:opacity-90", outline: "border border-border bg-card text-foreground hover:bg-muted", ghost: "text-foreground hover:bg-muted", destructive: "bg-destructive text-white hover:opacity-90" };
  return <button className={cn("inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-semibold transition-colors disabled:pointer-events-none disabled:opacity-50", variants[variant], className)} {...props} />;
}
