import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IMAGE_MODELS, VIDEO_MODELS, ModelSelectorModal } from "../src/components/ModelSelectorModal";

function renderModal(mode: "image" | "video") {
  return render(
    <ModelSelectorModal
      title={mode === "image" ? "이미지 모델 선택" : "비디오 모델 선택"}
      mode={mode}
      selectedModelId={mode === "image" ? "gpt-image-2" : "kling_o3_flf"}
      onSelect={() => {}}
      onClose={() => {}}
    />
  );
}

describe("ModelSelectorModal Renoise provider", () => {
  it("offers Renoise image models alongside Higgsfield ones", () => {
    renderModal("image");

    expect(screen.getByText("Nano Banana Pro (Renoise)")).toBeInTheDocument();
    expect(screen.getByText("GPT Image 2 (Renoise)")).toBeInTheDocument();
    expect(screen.getByText("Seedream 5.0 Pro (Renoise)")).toBeInTheDocument();
    expect(screen.getByText("Nano Banana Pro")).toBeInTheDocument();
  });

  it("offers Renoise video models alongside Higgsfield ones", () => {
    renderModal("video");

    expect(screen.getByText("Seedance 2.5 (Renoise)")).toBeInTheDocument();
    expect(screen.getByText("Kling 3.0 Omni (Renoise)")).toBeInTheDocument();
    expect(screen.getByText("MiniMax H3 (Renoise)")).toBeInTheDocument();
  });

  it("uses provider-prefixed ids so the backend can route the request", () => {
    const imageIds = IMAGE_MODELS.map((m) => m.id);
    const videoIds = VIDEO_MODELS.map((m) => m.id);

    expect(imageIds).toContain("renoise:nano-banana-pro");
    expect(imageIds).toContain("gpt-image-2");
    expect(videoIds).toContain("renoise:seedance-2.5-byteplus");
    expect(videoIds).toContain("kling_o3_flf");
  });
});
