"use client";

/** 全站右侧悬浮入口组：企业微信二维码 + 提建议；窄屏隐藏（页脚仍有入口）。 */

import { useState } from "react";
import { IconFeedback, IconWecom } from "./icons";
import { ContactModal } from "./ContactModal";
import { FeedbackModal } from "./FeedbackModal";

export function SideFab() {
  const [contactOpen, setContactOpen] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  return (
    <>
      <div className="side-fab-group">
        <button type="button" className="side-fab" aria-label="企业微信联系" onClick={() => setContactOpen(true)}>
          <IconWecom size={16} />
          <span>企业微信</span>
        </button>
        <button type="button" className="side-fab" aria-label="提建议" onClick={() => setFeedbackOpen(true)}>
          <IconFeedback size={16} />
          <span>提建议</span>
        </button>
      </div>
      <ContactModal open={contactOpen} onClose={() => setContactOpen(false)} />
      <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
    </>
  );
}
