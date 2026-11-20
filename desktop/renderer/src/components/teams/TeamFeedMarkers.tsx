import type { TeamRunOrigin } from "../../../../shared/teams.js";
import { TeamIcon } from "../InputPicker.js";

/** Quiet line before a Run that a team event started instead of the user. */
export function TeamRunMarker({ origin }: { origin: TeamRunOrigin | undefined }) {
  if (!origin) return null;
  const text = origin.origin === "user_message" ? "你的团队消息" : origin.member ? `团队续跑 · ${origin.member}` : "团队续跑";
  return <p className="goal-feed-marker team-feed-marker"><TeamIcon />{text}</p>;
}
