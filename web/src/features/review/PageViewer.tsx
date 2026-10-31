import { ChevronLeft, ChevronRight, Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { usePageImage } from "../../api/library";
import type { DocumentDetail } from "../../api/types";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { boxStyle, pageUnits, type Box } from "./geometry";
import styles from "./PageViewer.module.css";

export type Overlay = { field: string; box: Box; tone: "plain" | "flag" | "done"; label: string };

type Props = {
  document: DocumentDetail;
  page: number;
  onPage: (page: number) => void;
  overlays: Overlay[];
  selected: string | null;
  onSelect: (field: string) => void;
};

const ZOOMS = [0.75, 1, 1.25, 1.5, 2, 2.5];

/** One page at a time, fitted to the width; boxes are positioned in percent so they follow the zoom. */
export function PageViewer({ document, page, onPage, overlays, selected, onSelect }: Props) {
  const [zoom, setZoom] = useState(1);
  const info = document.pages.find((p) => p.n === page);
  const { url, isError } = usePageImage(document.id, info ? page : null);
  const selectedRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (url) selectedRef.current?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }, [selected, url, page, zoom]);

  const count = document.pages.length;
  const units = info ? pageUnits(info.width, info.height) : 1294;
  const zoomIndex = ZOOMS.indexOf(zoom);

  return (
    <div className={styles.viewer}>
      <div className={styles.toolbar}>
        <div className={styles.group}>
          <Button size="sm" variant="ghost" icon={<ChevronLeft />} aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)} />
          <span className={`${styles.pageLabel} num`}>
            Page {page} of {count}
          </span>
          <Button size="sm" variant="ghost" icon={<ChevronRight />} aria-label="Next page" disabled={page >= count} onClick={() => onPage(page + 1)} />
        </div>
        <div className={styles.group}>
          <Button size="sm" variant="ghost" icon={<ZoomOut />} aria-label="Zoom out" disabled={zoomIndex <= 0} onClick={() => setZoom(ZOOMS[zoomIndex - 1]!)} />
          <span className={`${styles.zoom} num`}>{Math.round(zoom * 100)}%</span>
          <Button size="sm" variant="ghost" icon={<ZoomIn />} aria-label="Zoom in" disabled={zoomIndex >= ZOOMS.length - 1} onClick={() => setZoom(ZOOMS[zoomIndex + 1]!)} />
          <Button size="sm" variant="ghost" icon={<Maximize2 />} aria-label="Fit width" title="Fit width" disabled={zoom === 1} onClick={() => setZoom(1)} />
        </div>
      </div>
      <div className={styles.scroller}>
        {!info ? (
          <p className={styles.message}>This document has no pages to show.</p>
        ) : (
          <div className={styles.sheet} style={{ width: `${zoom * 100}%`, aspectRatio: `${info.width} / ${info.height}` }}>
            {url ? (
              <img src={url} alt={`Page ${page} of ${document.title}`} className={styles.image} draggable={false} />
            ) : (
              <div className={styles.loading}>{isError ? "The page could not be rendered." : <Spinner size={20} label="Loading page" />}</div>
            )}
            {url &&
              overlays.map((o) => (
                <button
                  key={o.field}
                  ref={o.field === selected ? selectedRef : undefined}
                  type="button"
                  className={`${styles.box} ${styles[o.tone]} ${o.field === selected ? styles.selected : ""}`}
                  style={boxStyle(o.box, units)}
                  title={o.label}
                  aria-label={`Select ${o.label}`}
                  tabIndex={-1}
                  onClick={() => onSelect(o.field)}
                />
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
