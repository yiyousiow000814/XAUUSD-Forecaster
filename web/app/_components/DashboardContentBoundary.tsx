"use client";

import { Component, type ReactNode } from "react";

export default class DashboardContentBoundary extends Component<{
  href: string;
  children: ReactNode;
}, { href: string; failed: boolean }> {
  state = { href: this.props.href, failed: false };

  static getDerivedStateFromProps(props: { href: string }, state: { href: string }) {
    return props.href === state.href ? null : { href: props.href, failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <main className="app-view-error">
      <section className="current-data-notice audit-resource-notice is-error" role="alert">
        <b>页面内容暂不可用</b>
        <span>页面文件或数据未能正常显示。可以重新打开此页，或使用上方导航查看其他页面。</span>
        <button type="button" onClick={() => window.location.assign(this.props.href)}>重新打开页面</button>
      </section>
    </main>;
  }
}
