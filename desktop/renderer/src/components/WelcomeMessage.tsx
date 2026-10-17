import { EidosMark } from "./EidosMark.js";

export function WelcomeMessage() {
  return (
    <div className="empty-state">
      <div className="empty-hero">
        <EidosMark className="empty-logo" variant="hero" />
      </div>
      <h2>让思考抵达现实</h2>
      <p className="empty-subtitle">深度工作，理解复杂问题，分析关键脉络，持续推进每一项重要任务</p>
    </div>
  );
}
