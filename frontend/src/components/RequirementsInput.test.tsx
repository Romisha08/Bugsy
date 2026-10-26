import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RequirementsInput } from "./RequirementsInput";

describe("RequirementsInput Python targets", () => {
  it("enables the five supported Python targets", () => {
    render(<RequirementsInput onParsed={vi.fn()} onParseError={vi.fn()} onSourceChange={vi.fn()} busy={false} />);

    const selector = screen.getByLabelText("Python target") as HTMLSelectElement;
    expect(selector.value).toBe("3.12");
    expect(screen.getByText(/Sandbox image availability is unverified until the actual runtime is reported/)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "3.12" })).toBeEnabled();
    for (const version of ["3.10", "3.11", "3.12", "3.13", "3.14"]) {
      expect(screen.getByRole("option", { name: version })).toBeEnabled();
    }
  });
});
