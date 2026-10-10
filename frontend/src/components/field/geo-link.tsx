import { MapPin } from "lucide-react";

// Ссылка на точку в OpenStreetMap: геометка снимка или факта открывается на карте в новой вкладке.
export function GeoLink({
  latitude,
  longitude,
  accuracy,
  label,
}: {
  latitude: string;
  longitude: string;
  accuracy?: string | null;
  label: string;
}) {
  const lat = Number(latitude).toFixed(6);
  const lon = Number(longitude).toFixed(6);
  return (
    <a
      href={`https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=18/${lat}/${lon}`}
      target="_blank"
      rel="noreferrer noopener"
      aria-label={label}
      className="inline-flex min-h-8 items-center gap-1 rounded-sm text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <MapPin className="size-3.5 shrink-0" aria-hidden />
      <span className="tabular-nums">
        {Number(lat).toFixed(5)}, {Number(lon).toFixed(5)}
        {accuracy ? ` · ±${Math.round(Number(accuracy))} м` : ""}
      </span>
    </a>
  );
}
