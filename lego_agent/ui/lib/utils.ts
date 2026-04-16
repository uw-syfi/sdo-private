import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Strip ANSI escape sequences (e.g. \x1b[37m) from terminal output before
// rendering in the browser, where the ESC byte is invisible but the rest
// of the sequence (like "[37m") shows as literal text.
export function stripAnsi(str: string): string {
  return str.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '');
}
