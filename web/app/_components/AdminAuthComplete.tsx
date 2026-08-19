"use client";

import { useEffect } from "react";
import { ADMIN_AUTH_MESSAGE_TYPE } from "../_lib/admin-auth-session";

export default function AdminAuthComplete() {
  useEffect(() => {
    if (!window.opener || window.opener.closed) return;
    window.opener.postMessage({ type: ADMIN_AUTH_MESSAGE_TYPE }, window.location.origin);
    window.close();
  }, []);

  return <main className="admin-auth-complete">
    <h1>管理员认证已完成</h1>
    <p>正在返回管理后台；如果此窗口未自动关闭，请手动关闭并返回原窗口。</p>
  </main>;
}
