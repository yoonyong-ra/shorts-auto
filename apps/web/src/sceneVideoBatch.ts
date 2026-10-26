import type { VideoSceneItem } from "./types";

export interface VideoBatchPlan {
  /** 아직 비디오가 없는 씬들만 (재실행 시 이어서 생성할 대상) */
  readonly pending: readonly VideoSceneItem[];
  /** 이미 비디오가 있어서 건너뛴 씬 수 */
  readonly skippedCount: number;
}

/**
 * 일괄 비디오 생성 계획을 계산한다.
 * 이미 `generated_video_url`이 있는 씬은 다시 생성하지 않고 건너뛴다.
 */
export function planVideoBatch(videoScenes: readonly VideoSceneItem[]): VideoBatchPlan {
  const pending = videoScenes.filter((scene) => !scene.generated_video_url);
  return {
    pending,
    skippedCount: videoScenes.length - pending.length
  };
}
