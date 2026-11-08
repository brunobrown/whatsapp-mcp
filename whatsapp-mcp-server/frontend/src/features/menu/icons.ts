import {
  Activity,
  Clock,
  Database,
  Eye,
  EyeOff,
  History,
  Image as ImageIcon,
  Lock,
  Monitor,
  Radio,
  Settings,
  Shield,
  User,
  Webhook,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

/** The icons the sections can name. An unknown name falls back to a gear. */
export const ICONS: Record<string, LucideIcon> = {
  activity: Activity,
  clock: Clock,
  database: Database,
  eye: Eye,
  "eye-off": EyeOff,
  history: History,
  image: ImageIcon,
  lock: Lock,
  monitor: Monitor,
  radio: Radio,
  shield: Shield,
  user: User,
  webhook: Webhook,
};

/** The icon of an entry that names none the interface knows. */
export const FALLBACK_ICON: LucideIcon = Settings;
