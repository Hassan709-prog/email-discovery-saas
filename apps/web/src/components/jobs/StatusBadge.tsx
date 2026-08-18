import React from 'react';
import { ScanJobStatus } from '@/types/api';

interface StatusBadgeProps {
  status: ScanJobStatus;
  className?: string;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, className = '' }) => {
  let label = status as string;
  let styleClasses = 'bg-slate-100 text-slate-800 border-slate-200';

  switch (status) {
    case 'DRAFT':
      label = 'Not Started';
      styleClasses = 'bg-slate-100 text-slate-700 border-slate-200';
      break;
    case 'QUEUED':
      label = 'Waiting';
      styleClasses = 'bg-amber-50 text-amber-800 border-amber-200';
      break;
    case 'RUNNING':
      label = 'Scanning';
      styleClasses = 'bg-indigo-50 text-indigo-700 border-indigo-200';
      break;
    case 'CANCELLING':
      label = 'Cancelling';
      styleClasses = 'bg-orange-50 text-orange-800 border-orange-200';
      break;
    case 'CANCELLED':
      label = 'Cancelled';
      styleClasses = 'bg-slate-100 text-slate-600 border-slate-300';
      break;
    case 'COMPLETED':
      label = 'Completed';
      styleClasses = 'bg-emerald-50 text-emerald-800 border-emerald-200';
      break;
    case 'COMPLETED_WITH_ERRORS':
      label = 'Completed with Some Issues';
      styleClasses = 'bg-amber-50 text-amber-800 border-amber-200';
      break;
    case 'FAILED':
      label = 'Failed';
      styleClasses = 'bg-rose-50 text-rose-800 border-rose-200';
      break;
  }

  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${styleClasses} ${className}`}
    >
      {status === 'RUNNING' && (
        <span className="w-1.5 h-1.5 mr-1.5 rounded-full bg-blue-500 animate-ping" />
      )}
      {label}
    </span>
  );
};
