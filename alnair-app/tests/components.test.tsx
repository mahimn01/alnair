import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ComponentView } from "../src/ComponentView";
import { validate } from "../src/spec";
import { gallery } from "../src/demo";
describe("fixed renderer", () => {
  for (const spec of gallery)
    it(`renders ${spec.type} ${spec.id}`, () => {
      render(<ComponentView spec={validate(spec)} onAnswer={() => {}} />);
      expect(screen.getByTestId(`component-${spec.id}`)).toBeInTheDocument();
    });
  it("answers forms only after user submits", () => {
    const answer = vi.fn();
    render(
      <ComponentView
        spec={validate({
          id: "f",
          type: "form",
          title: "Decide",
          fields: [{ name: "name", label: "Name", required: true }],
        })}
        onAnswer={answer}
      />,
    );
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Alnair" },
    });
    expect(answer).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Send answer"));
    expect(answer).toHaveBeenCalledWith("f", { name: "Alnair" });
  });
  it("returns the selected choice", () => {
    const answer = vi.fn();
    render(
      <ComponentView
        spec={validate({
          id: "b",
          type: "choices",
          title: "Choose",
          options: ["Mac", "Windows"],
        })}
        onAnswer={answer}
      />,
    );
    fireEvent.click(screen.getByText("Mac"));
    expect(answer).toHaveBeenCalledWith("b", { choice: "Mac" });
  });
  it("never renders markdown HTML or javascript links", () => {
    const { container } = render(
      <ComponentView
        spec={validate({
          id: "m",
          type: "markdown",
          text: "<script>alert(1)</script>\n[x](javascript:alert(1))",
        })}
        onAnswer={() => {}}
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector('[href^="javascript:"]')).toBeNull();
  });
  it.each(["html", "script", "iframe"])("rejects %s", (type) => {
    expect(() => validate({ id: "bad", type })).toThrow();
  });
  it("rejects unknown keys", () =>
    expect(() =>
      validate({
        id: "a",
        type: "card",
        title: "a",
        body: "b",
        onClick: "evil",
      }),
    ).toThrow());
  it("rejects remote tracking images", () =>
    expect(() =>
      validate({
        id: "a",
        type: "image",
        src: "https://evil.test/image",
        alt: "a",
      }),
    ).toThrow());
  it("rejects oversized charts", () =>
    expect(() =>
      validate({
        id: "a",
        type: "chart",
        title: "a",
        kind: "bar",
        points: Array(201).fill({ label: "x", value: 1 }),
      }),
    ).toThrow());
});
it("does not claim an answer was sent when bridge rejects it", async () => {
  render(
    <ComponentView
      spec={validate({
        id: "r",
        type: "choices",
        title: "Choose",
        options: ["Try"],
      })}
      onAnswer={async () => {
        throw Error("Question expired");
      }}
    />,
  );
  fireEvent.click(screen.getByText("Try"));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Question expired",
  );
  expect(screen.getByText("Try")).not.toBeDisabled();
});
