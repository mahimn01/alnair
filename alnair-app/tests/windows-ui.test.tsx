import React from "react";
import { it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
vi.mock("../src/TerminalPane", () => ({
  native: () => false,
  TerminalPane: React.forwardRef(() => <div />),
}));
import App from "../src/App";
it("does not pretend the Mac harness is installed on Windows", () => {
  vi.spyOn(window.navigator, "platform", "get").mockReturnValue("Win32");
  render(<App />);
  expect(
    screen.getByText(/Memory unavailable on this machine/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Research unavailable on this machine/),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("option", { name: "Claude default (no harness routing)" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/WINDOWS PROTOTYPE/)).toBeInTheDocument();
  vi.restoreAllMocks();
});
