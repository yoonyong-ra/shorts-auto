import { describe, expect, it } from "vitest";

import { planVideoBatch } from "../src/sceneVideoBatch";
import type { VideoSceneItem } from "../src/types";

function makeScene(sceneId: number, hasVideo: boolean): VideoSceneItem {
  return {
    scene_id: sceneId,
    target_duration: 2.5,
    start: 0,
    end: 2.5,
    duration_frames: 60,
    narration: "",
    start_image_prompt: "",
    start_image_url: `/api/projects/p1/production/scenes/scene_${sceneId}_start.png`,
    video_prompt: "",
    generated_video_url: hasVideo ? `/api/projects/p1/production/scenes/scene_${sceneId}_video.mp4` : null,
    final_frame_url: null,
    continuity: {
      camera: "",
      subject_state: "",
      lighting: "",
      handoff_description: "",
      prev_final_frame_url: null
    },
    is_locked: false
  };
}

describe("planVideoBatch", () => {
  it("아무 비디오도 없으면 모든 씬을 대상으로 잡는다", () => {
    const scenes = [makeScene(1, false), makeScene(2, false), makeScene(3, false)];
    const plan = planVideoBatch(scenes);

    expect(plan.skippedCount).toBe(0);
    expect(plan.pending.map((s) => s.scene_id)).toEqual([1, 2, 3]);
  });

  it("이미 생성된 비디오가 있는 씬은 건너뛰고 남은 씬부터 이어서 진행한다", () => {
    const scenes = [1, 2, 3, 4, 5, 6, 7].map((id) => makeScene(id, id <= 5));
    const plan = planVideoBatch(scenes);

    expect(plan.skippedCount).toBe(5);
    expect(plan.pending.map((s) => s.scene_id)).toEqual([6, 7]);
  });

  it("모든 씬에 비디오가 있으면 생성 대상이 비어 있다 (렌더링만 수행)", () => {
    const scenes = [makeScene(1, true), makeScene(2, true)];
    const plan = planVideoBatch(scenes);

    expect(plan.skippedCount).toBe(2);
    expect(plan.pending).toHaveLength(0);
  });
});
