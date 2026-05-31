import PropTypes from 'prop-types';
import { BaseEdge, getSmoothStepPath } from 'reactflow';

/**
 * Custom Relationship Edge Component
 * Shows clear join column label between related tables.
 */
const RelationshipEdge = ({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
  style,
}) => {
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 12,
  });

  const sourceColumn =
    data?.sourceColumn ||
    data?.source_column ||
    data?.relationship?.source_column ||
    data?.relationship?.from_column ||
    data?.relationship?.source?.column ||
    '';

  const targetColumn =
    data?.targetColumn ||
    data?.target_column ||
    data?.relationship?.target_column ||
    data?.relationship?.to_column ||
    data?.relationship?.target?.column ||
    '';

  const sourceTable =
    data?.sourceTable ||
    data?.relationship?.source_table ||
    data?.relationship?.from_table ||
    data?.relationship?.source?.table ||
    '';

  const targetTable =
    data?.targetTable ||
    data?.relationship?.target_table ||
    data?.relationship?.to_table ||
    data?.relationship?.target?.table ||
    '';

  const confidenceLevel =
    data?.confidenceLevel ||
    data?.relationship?.confidence_level ||
    data?.relationship?.confidence ||
    'HIGH';

  const label =
    sourceColumn && targetColumn
      ? `${sourceColumn} → ${targetColumn}`
      : 'Relationship';

  const tooltip =
    sourceTable && targetTable
      ? `${sourceTable}.${sourceColumn} → ${targetTable}.${targetColumn}`
      : label;

  const confidenceClass = (() => {
    const level = String(confidenceLevel).toUpperCase();

    if (level === 'HIGH') {
      return 'bg-green-50 text-green-700 border-green-300';
    }

    if (level === 'MEDIUM') {
      return 'bg-orange-50 text-orange-700 border-orange-300';
    }

    return 'bg-blue-50 text-blue-700 border-blue-300';
  })();

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: '#2563eb',
          strokeWidth: 3,
          opacity: 1,
          ...style,
        }}
      />

      <foreignObject
        width={220}
        height={54}
        x={labelX - 110}
        y={labelY - 27}
        className="overflow-visible"
      >
        <div className="flex items-center justify-center w-full h-full pointer-events-none">
          <div
            className={`
              max-w-[210px]
              px-3 py-1.5
              rounded-md
              text-[11px]
              font-semibold
              border
              bg-white
              text-gray-900
              shadow-md
              truncate
            `}
            title={tooltip}
          >
            {label}

            <span
              className={`
                ml-2
                px-1.5 py-0.5
                rounded
                border
                text-[9px]
                ${confidenceClass}
              `}
            >
              {String(confidenceLevel).toUpperCase()}
            </span>
          </div>
        </div>
      </foreignObject>
    </>
  );
};

RelationshipEdge.propTypes = {
  id: PropTypes.string.isRequired,
  sourceX: PropTypes.number.isRequired,
  sourceY: PropTypes.number.isRequired,
  targetX: PropTypes.number.isRequired,
  targetY: PropTypes.number.isRequired,
  sourcePosition: PropTypes.string,
  targetPosition: PropTypes.string,
  data: PropTypes.shape({
    sourceColumn: PropTypes.string,
    targetColumn: PropTypes.string,
    source_column: PropTypes.string,
    target_column: PropTypes.string,
    sourceTable: PropTypes.string,
    targetTable: PropTypes.string,
    confidenceLevel: PropTypes.string,
    relationship: PropTypes.object,
  }),
  markerEnd: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  style: PropTypes.object,
};

export default RelationshipEdge;