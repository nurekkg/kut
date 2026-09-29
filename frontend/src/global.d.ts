import ru from "../messages/ru.json";

declare module "next-intl" {
  interface AppConfig {
    Locale: "ru";
    Messages: typeof ru;
  }
}
