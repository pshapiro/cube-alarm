import React, { useEffect, useRef } from 'react';
import "cubing/twisty";
import './CubeViewer.css';

const TwistyPlayer: React.FC<any> = (props) => (
  React.createElement('twisty-player', props)
);

interface CubeViewerProps {
  moves: string[];
}

const CubeViewer: React.FC<CubeViewerProps> = ({ moves }) => {
  const playerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const player = playerRef.current?.querySelector('twisty-player') as any;
    if (player) {
      player.alg = moves.join(' ');
      player.experimentalDisplay = '2d';
    }
  }, [moves]);

  return (
    <div className="cube-viewer" ref={playerRef}>
      <TwistyPlayer
        background="none"
        control-panel="none"
        puzzle="3x3x3"
        playback="manual"
      ></TwistyPlayer>
    </div>
  );
};

export default CubeViewer;
