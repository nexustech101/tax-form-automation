/**
 * The page registry: every route of the dashboard. A page is a folder under features/ plus one entry here;
 * `nav` entries appear in the sidebar in this order. `fullBleed` pages fill the content area (no padding,
 * no page scroll) and manage their own scrolling.
 */
import { FileText, LayoutDashboard, Settings, Sparkles, type LucideIcon } from "lucide-react";
import type { ComponentType } from "react";
import { AssistantPage } from "./assistant/AssistantPage";
import { DocumentsPage } from "./documents/DocumentsPage";
import { OverviewPage } from "./overview/OverviewPage";
import { ReviewPage } from "./review/ReviewPage";
import { SettingsPage } from "./settings/SettingsPage";

export type Page = {
  path: string;
  label: string;
  component: ComponentType;
  icon?: LucideIcon;
  nav?: boolean;
  fullBleed?: boolean;
};

export const pages: Page[] = [
  { path: "/", label: "Overview", icon: LayoutDashboard, component: OverviewPage, nav: true },
  { path: "/documents", label: "Documents", icon: FileText, component: DocumentsPage, nav: true },
  { path: "/documents/:id", label: "Review", component: ReviewPage, fullBleed: true },
  { path: "/assistant", label: "Assistant", icon: Sparkles, component: AssistantPage, nav: true, fullBleed: true },
  { path: "/settings", label: "Settings", icon: Settings, component: SettingsPage, nav: true },
];
