export type StadiumParkingMapKind = "entrance" | "preferred-area" | "nearby-alternatives" | "access-gates";

export type StadiumParkingMap = {
  stadiumCode: string;
  stadiumName: string;
  src: string;
  width: number;
  height: number;
  kind: StadiumParkingMapKind;
  title: string;
  summary: string;
  visualNotes: readonly string[];
  sourcePageUrl: string;
  credit: string;
  capturedAt: string;
};
