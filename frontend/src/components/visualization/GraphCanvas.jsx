import { useCallback, useEffect, useMemo } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  ReactFlowProvider,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';

import { useGraphStore } from '../../stores/graphStore.js';
import FileNode from './FileNode.jsx';
import ColumnNode from './ColumnNode.jsx';
import RelationshipEdge from './RelationshipEdge.jsx';
import GraphControls from './GraphControls.jsx';

const nodeTypes = {
  fileNode: FileNode,
  columnNode: ColumnNode,
};

const edgeTypes = {
  relationship: RelationshipEdge,
};

function normalizeEdges(edges = []) {
  return edges
    .filter((edge) => edge && edge.source && edge.target && edge.source !== edge.target)
    .map((edge) => ({
      ...edge,
      type: edge.type || 'relationship',
      animated: false,
      interactionWidth: 30,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 22,
        height: 22,
        color: '#2563eb',
      },
      style: {
        stroke: '#2563eb',
        strokeWidth: 3,
        opacity: 1,
        ...(edge.style || {}),
      },
      labelStyle: {
        fill: '#111827',
        fontWeight: 600,
        fontSize: 12,
        ...(edge.labelStyle || {}),
      },
      labelBgStyle: {
        fill: '#ffffff',
        fillOpacity: 0.95,
      },
      data: {
        ...(edge.data || {}),
        sourceColumn:
          edge.data?.sourceColumn ||
          edge.data?.source_column ||
          edge.sourceColumn ||
          edge.source_column ||
          '',
        targetColumn:
          edge.data?.targetColumn ||
          edge.data?.target_column ||
          edge.targetColumn ||
          edge.target_column ||
          '',
      },
    }));
}

const GraphCanvas = () => {
  const nodes = useGraphStore((state) => state.nodes);
  const filteredEdges = useGraphStore((state) => state.filteredEdges);
  const selectRelationship = useGraphStore(
    (state) => state.actions?.selectRelationship
  );

  const visibleEdges = useMemo(
    () => normalizeEdges(filteredEdges),
    [filteredEdges]
  );

  const [nodesState, setNodes, onNodesChange] = useNodesState(nodes || []);
  const [edgesState, setEdges, onEdgesChange] = useEdgesState(visibleEdges);

  const tableCount = useMemo(() => {
    return nodesState.filter((node) => node.type === 'fileNode').length;
  }, [nodesState]);

  const columnCount = useMemo(() => {
    return nodesState.filter((node) => node.type === 'columnNode').length;
  }, [nodesState]);

  useEffect(() => {
    setNodes(nodes || []);
  }, [nodes, setNodes]);

  useEffect(() => {
    setEdges(visibleEdges);
  }, [visibleEdges, setEdges]);

  const onEdgeClick = useCallback(
    (event, edge) => {
      event.preventDefault();

      if (typeof selectRelationship === 'function') {
        selectRelationship(edge.id);
      }
    },
    [selectRelationship]
  );

  const onNodeClick = useCallback((_event, _node) => {
    // Keep node click available for future table/column details.
  }, []);

  if (!nodes || nodes.length === 0) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-gray-50 rounded-lg border-2 border-dashed border-gray-300">
        <div className="text-center">
          <svg
            className="w-16 h-16 text-gray-400 mx-auto mb-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
            />
          </svg>

          <p className="text-gray-600 text-lg font-medium">
            No tables found
          </p>

          <p className="text-gray-500 text-sm mt-2">
            Upload ThoughtSpot metadata and source tables to discover relationships.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-full bg-gray-50 rounded-lg overflow-hidden border border-gray-200 relative">
      <ReactFlow
        nodes={nodesState}
        edges={edgesState}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onEdgeClick={onEdgeClick}
        onNodeClick={onNodeClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{
          padding: 0.25,
          minZoom: 0.5,
          maxZoom: 1.25,
        }}
        attributionPosition="bottom-left"
        minZoom={0.25}
        maxZoom={2}
        elevateEdgesOnSelect
        defaultEdgeOptions={{
          type: 'relationship',
          interactionWidth: 30,
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: '#2563eb',
          },
          style: {
            stroke: '#2563eb',
            strokeWidth: 3,
            opacity: 1,
          },
        }}
      >
        <Background color="#cbd5e1" gap={16} size={1} variant="dots" />

        <Controls
          showInteractive={false}
          className="!bg-white !border-gray-300 !shadow-lg"
        />

        <MiniMap
          nodeColor={(node) => {
            if (node.type === 'fileNode') return '#2563eb';
            if (node.type === 'columnNode') return '#93c5fd';
            return '#e0e7ff';
          }}
          maskColor="rgba(0, 0, 0, 0.1)"
          className="!bg-white !border-gray-300"
        />
      </ReactFlow>

      <div className="absolute left-4 top-4 z-20 bg-white border border-gray-200 rounded-lg shadow px-3 py-2 text-xs text-gray-700">
        <span className="font-semibold">{tableCount}</span> tables
        <span className="mx-2">•</span>
        <span className="font-semibold">{columnCount}</span> columns
        <span className="mx-2">•</span>
        <span className="font-semibold">{edgesState.length}</span> relationships
      </div>

      <GraphControls />
    </div>
  );
};

const GraphCanvasWrapper = () => (
  <ReactFlowProvider>
    <GraphCanvas />
  </ReactFlowProvider>
);

export default GraphCanvasWrapper;