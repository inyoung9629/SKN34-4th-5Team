"use client";

import Image from "next/image";
import { useId, useRef } from "react";
import { Icon } from "@/components/icons";
import type { StadiumParkingMap } from "@/lib/stadium-parking-maps";

type StadiumParkingMapDialogProps = {
  parking?: StadiumParkingMap;
  className?: string;
};

export function StadiumParkingMapDialog({ parking, className = "" }: StadiumParkingMapDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const id = useId();

  if (!parking) return null;

  const titleId = `parking-map-title-${parking.stadiumCode.toLowerCase()}-${id}`;
  const descriptionId = `parking-map-description-${parking.stadiumCode.toLowerCase()}-${id}`;

  function closeDialog() {
    dialogRef.current?.close();
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`stadium-parking-trigger ${className}`.trim()}
        aria-haspopup="dialog"
        onClick={() => dialogRef.current?.showModal()}
      >
        <Icon name="pin" size={15} />
        주차장 위치 보기
      </button>
      <dialog
        ref={dialogRef}
        className="stadium-parking-dialog"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onCancel={(event) => {
          event.preventDefault();
          closeDialog();
        }}
        onClose={() => triggerRef.current?.focus()}
        onClick={(event) => {
          if (event.target === event.currentTarget) closeDialog();
        }}
      >
        <div className="stadium-parking-dialog-panel">
          <header className="stadium-parking-dialog-header">
            <div>
              <span>주차 안내</span>
              <h2 id={titleId}>{parking.stadiumName}</h2>
            </div>
            <button type="button" className="stadium-parking-dialog-close" aria-label="주차장 위치 안내 닫기" onClick={closeDialog}>×</button>
          </header>
          <div className="stadium-parking-dialog-image">
            <Image
              src={parking.src}
              alt={`${parking.stadiumName} ${parking.title} 위치 안내`}
              width={parking.width}
              height={parking.height}
              unoptimized
            />
          </div>
          <footer className="stadium-parking-dialog-footer">
            <div>
              <strong>{parking.title}</strong>
              <p id={descriptionId}>{parking.summary}</p>
            </div>
            {parking.sourcePageUrl && <a href={parking.sourcePageUrl} target="_blank" rel="noopener noreferrer">출처: {parking.credit} ↗</a>}
          </footer>
        </div>
      </dialog>
    </>
  );
}
